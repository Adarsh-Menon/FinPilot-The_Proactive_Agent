# Iteration 18: Phone alerts, health board, AI budget, abuse controls and an admin audit log

Date: 2026-10-27. Author: Claude Code. Request from the user: admin alerts sent to their
phone on error, and the important things missing from the admin portal. Decisions accepted:
**Telegram** as the phone channel; all four additions (alerts page and health board, AI cost
budget, abuse and limits view with suspension, admin audit log).

## Phone alerts

The API raises alerts itself, so they work without the Prometheus/Grafana stack running.
Every alert is stored for the portal and pushed to Telegram when `TELEGRAM_BOT_TOKEN` and
`TELEGRAM_CHAT_ID` are set. The same alert is pushed at most once every 15 minutes; repeats
raise its count. Alerts carry no customer data: what broke, where, how often.

| Alert | Severity | When |
|---|---|---|
| Server error on METHOD /route | critical | any unhandled exception (HTTP 500) |
| AI calls failing: agent | warning | 3 failed or incomplete model calls for an agent in 10 minutes |
| Unusual number of failed sign-ins | warning | more than 30 failed or throttled sign-ins in 5 minutes |
| Possible abuse by u-xxxxxx | warning | one account hits usage limits 5 times in an hour |
| AI spend at 80% of today's budget | warning | daily spend reaches 80% of the cap |
| Daily AI budget reached | critical | daily spend reaches the cap (AI auto-paused if enabled) |

Bookkeeping (spend, limit hits, alert writes) runs on one background worker, so requests never
wait on it; on SQLite it writes as soon as the request's own transaction commits.

## Admin portal

- **Alerts & health:** tiles for API uptime, database check and size, AI mode and last
  successful model call, 5xx errors and 95th-percentile response time in the last hour, model
  calls and failure rate today, spend against the cap and open alerts; Telegram status with a
  test button; the alert list with acknowledge and resolve. Refreshes every 30 seconds.
- **AI costs:** today, this month and the cap; a 30-day spend chart (one column per day, cap as
  a dashed line, hover for calls and failures); spend by model. Cost comes from the configured
  model prices; unpriced models count as $0 and are marked. The cap and auto-pause are set here.
- **Abuse & limits:** for the last 7 days, accounts by pseudonym with the limits they hit
  (failed sign-in, 20 AI categorizations a day, 20 chat questions an hour, 40 voice clips an
  hour). Suspend (with a reason) blocks sign-in and every customer request at once; unsuspend
  restores access.
- **Audit log:** every operator action with the operator's username (policy changes, budget
  changes, suspensions, alert acknowledge/resolve, test alerts) and automatic ones by "system",
  such as a budget pause.

## Data, security and evaluation impact

New tables `alerts`, `ai_spend`, `ops_settings`, `suspensions` and `admin_audit`, none holding
customer content. The PostgreSQL migration grants the app role what it writes where errors and
model calls happen (alerts, spend) and read access to budgets and suspensions; operations can
manage alerts, budgets, suspensions and the audit log. It also grants the app role read access
to only three activity columns (who, what, when), which the daily AI-request limit from
iteration 17 needs and the migration had not granted. The Telegram bot token is a secret in
`.env`; it is never logged. Suspension is by pseudonym, so operators still never see usernames.

## Acceptance and evidence

- Tests: repeated alerts push once and count up; an unhandled error raises a delivered alert,
  operators acknowledge and resolve it and both appear in the audit log; a test alert is sent;
  the daily budget alerts and pauses AI, after which model calls are refused, with a system audit
  entry; failed sign-ins show in the abuse view by pseudonym only; suspension blocks live
  sessions and sign-in, and unsuspension restores them. Backend suite: 155 passed, 3 PostgreSQL
  tests skipped. Frontend build passed.
- Browser (throwaway database and operator, synthetic data): six failed sign-ins produced a
  "Possible abuse" alert and a row in Abuse & limits; suspending it made sign-in return 403;
  the budget change, suspension and acknowledgement appeared in the audit log; health tiles,
  the 30-day spend chart and tooltips rendered with no page errors and no horizontal scroll at
  phone width. Real Telegram delivery was not tested here (no bot token yet).
