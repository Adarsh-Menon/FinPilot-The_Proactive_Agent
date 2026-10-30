"""Operations alerts: recorded for the admin portal and pushed to the operator's phone by Telegram.

An alert names what broke, where and how often: never usernames, amounts, descriptions or any
text a customer typed. The same alert key is pushed at most once per quiet period; repeats only
raise the count on the open alert, so a crash loop sends one message, not hundreds.
"""
import queue
import threading
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from .storage import Alert

QUIET = timedelta(minutes=15)
ICONS = {"critical": "🔴", "warning": "🟠", "info": "🔵"}


def now():
    """Current UTC time without timezone info (how the database stores times).

    Returns:
        A naive datetime.

    Used by: Alerter.raise_alert().
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Background:
    """One worker thread for operations bookkeeping (spend, limit hits, alerts), so requests never wait on it.

    Used by: create_app() in main: model-call costs, limit hits and alert writes are submitted here.
    """

    def __init__(self):
        """Start the worker thread with an empty job queue."""
        self.jobs = queue.Queue()
        threading.Thread(target=self._run, daemon=True).start()

    def submit(self, fn, *args):
        """Queue a job to run in the background.

        Args:
            fn: function to call.
            args: its arguments.

        Used by: on_model_call, limit_hit and the server-error alert in main.
        """
        self.jobs.put((fn, args))

    def drain(self):
        """Wait until every submitted job has run.

        Used by: Tests, before checking recorded data.
        """
        self.jobs.join()

    def _run(self):
        """Worker loop: run each job, swallowing errors so bookkeeping never takes the app down."""
        while True:
            fn, args = self.jobs.get()
            try:
                fn(*args)
            except Exception:
                pass        # bookkeeping must never take the app down; failures surface as missing data
            finally:
                self.jobs.task_done()


class Alerter:
    """Operations alerts: stored for the admin portal and pushed to the operator's phone by Telegram.

    Alerts say what broke, where and how often, never customer data. The same key is pushed at most once per 15
    minutes.

    Used by: create_app() in main. Seen on Admin > Alerts & health and in Telegram.
    """
    def __init__(self, sessions, token="", chat_id="", transport=None):
        """Configure the alerter.

        Args:
            sessions: database session factory.
            token: Telegram bot token (empty disables pushing).
            chat_id: Telegram chat id to message.
            transport: optional HTTP transport for tests.
        """
        self.sessions, self.token, self.chat_id = sessions, token, chat_id
        self.http = httpx.Client(transport=transport, timeout=8, trust_env=False)
        self.lock = threading.Lock()

    @property
    def configured(self):
        """True when both a bot token and chat id are set.

        Returns:
            bool.

        Used by: Admin > Alerts & health ("Telegram is set up").
        """
        return bool(self.token and self.chat_id)

    def raise_alert(self, key, severity, title, detail=""):
        """Record an alert (or count a repeat) and push it unless the same key was pushed recently.

        Args:
            key: dedupe key, e.g. "5xx:GET /api/v1/goals".
            severity: 'critical', 'warning' or 'info'.
            title: short headline.
            detail: one or two content-free sentences.

        Returns:
            The alert's id.

        Used by: Server errors, failing AI calls, sign-in spikes, abuse and budget checks in main.
        """
        with self.lock, self.sessions() as db:
            alert = db.scalar(select(Alert).where(Alert.key == key, Alert.status != "resolved").order_by(Alert.created_at.desc()))
            stamp = now()
            if alert is None:
                alert = Alert(key=key[:80], severity=severity, title=title[:120], detail=detail[:300])
                db.add(alert)
            else:
                alert.count += 1
                alert.last_at, alert.detail, alert.severity = stamp, detail[:300] or alert.detail, severity
            push = alert.sent_at is None or stamp - alert.sent_at >= QUIET
            if push:
                alert.sent_at = stamp
            db.commit()
            text, alert_id = self.format(alert), alert.id
        if push:
            threading.Thread(target=self._deliver, args=(alert_id, text), daemon=True).start()
        return alert_id

    def format(self, alert):
        """The Telegram message text for an alert.

        Args:
            alert: the Alert row.

        Returns:
            Text with severity icon, repeat count, title, detail and time.
        """
        repeat = f" (×{alert.count})" if alert.count > 1 else ""
        return (f"{ICONS.get(alert.severity, '⚪')} FinPilot {alert.severity.upper()}{repeat}\n{alert.title}\n"
            f"{alert.detail}\n{alert.last_at:%Y-%m-%d %H:%M} UTC").strip()

    def send_text(self, text):
        """Push one message to Telegram.

        Args:
            text: message text.

        Returns:
            True when Telegram accepted it.

        Used by: _deliver() and the admin "Send test alert" button.
        """
        if not self.configured:
            return False
        try:
            response = self.http.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True})
            return response.status_code == 200
        except httpx.HTTPError:
            return False   # never log the response: the URL carries the bot token

    def _deliver(self, alert_id, text):
        """Send an alert in a background thread and record whether it was delivered.

        Args:
            alert_id: the alert's id.
            text: message text.
        """
        delivered = self.send_text(text)
        with self.sessions() as db:
            alert = db.get(Alert, alert_id)
            if alert is not None:
                alert.delivered = alert.delivered or delivered
                db.commit()
