"""Investment agent: readiness and education, never a specific security to buy or sell.

It checks whether investing fits the owner's position (an emergency fund of three months
of essentials, no high-interest debt, a monthly surplus after goals), suggests a cautious
SIP amount, and explains broad options with their risks. Market headlines from public RSS
feeds give context only; they never drive a recommendation. Recommending named securities
would be regulated investment advice, which FinPilot does not give.
"""
import re
import time
from datetime import date, timedelta
from xml.etree import ElementTree
import httpx
from .advisor import ESSENTIAL
from .debts import HIGH_INTEREST

EMERGENCY_MONTHS = 3
SIP_SHARE = 0.5          # invest at most half of the surplus left after goals
MIN_SIP = 50_000         # ₹500, the usual minimum SIP
EDUCATION = [
    {"id": "index_fund", "name": "Broad market index fund", "risk": "Moves with the whole market; can fall 20–40% in bad years",
        "horizon": "5+ years", "why": "Low cost and diversified across many companies, so no single company decides the outcome."},
    {"id": "debt_fund", "name": "Short-duration debt fund", "risk": "Lower and steadier than equity, but not risk-free; interest-rate and credit risk",
        "horizon": "1–3 years", "why": "Suits money you may need sooner, such as a goal two years away."},
    {"id": "hybrid_fund", "name": "Balanced or hybrid fund", "risk": "Mix of equity and debt; falls less than pure equity but still can fall",
        "horizon": "3–5 years", "why": "A middle path if market swings would make you stop investing."},
    {"id": "gold", "name": "Gold (ETF or sovereign gold bonds)", "risk": "Price can stay flat or fall for years",
        "horizon": "5+ years, as a small share", "why": "Tends to behave differently from shares, so a small share can steady a portfolio."},
]
_news_cache = {}
# Tip-style headlines ("stocks to buy", "target price", "36% upside") read as recommendations; skip them.
TIPS = re.compile(r"\b(to buy|to sell|buy call|sell call|buy or sell|should you buy|target price|price target|stock target|targets? (?:raised|cut|hiked)|"
    r"buying opportunity|opportunity for investors|upside|multibagger|"
    r"stocks? picks?|top picks?|hot stocks?|returns? of \d)", re.I)


def readiness(profile, transactions, goals, debts, pace):
    """Whether investing fits the owner yet: three checks with the numbers behind each, and a cautious SIP.

    Checks: an emergency fund of three months of essential spending, no debt at 12% a year or more, and money left
    each month after goals. Never names a security.

    Args:
        profile: the owner's saved context (as_of, opening_balance_minor).
        transactions: the owner's ledger rows.
        goals: active goals with calculated progress.
        debts: debts the owner tracks.
        pace: monthly balance change from the latest forecast, minor units, or None without a forecast.

    Returns:
        Dict with ready, status, checks (id, passed, facts, why), sip_minor (half the surplus in ₹500 steps, or
        None), first_fix and the education list.

    Used by: GET /api/v1/invest and the Ready to Grow achievement. Seen on the Invest page (readiness checks and SIP
    amount).
    """
    as_of = profile["as_of"]
    start = (date.fromisoformat(as_of) - timedelta(days=29)).isoformat()
    essentials = sum(-t["amount_minor"] for t in transactions if t["kind"] in {"expense", "repayment"} and t["amount_minor"] < 0
        and start <= t["date"] <= as_of and ESSENTIAL.search(t.get("category") or "")) or None
    cash = profile["opening_balance_minor"]
    months = round(cash / essentials, 1) if cash is not None and essentials else None
    costly = [d for d in debts if d["kind"] != "rent" and d["balance_minor"] > 0 and d["rate_percent"] >= HIGH_INTEREST]
    goal_need = sum(g["progress"]["needed_per_month_minor"] for g in goals
        if g["status"] == "active" and g["progress"]["status"] not in {"reached", "past_due"})
    surplus = None if pace is None else pace - goal_need
    checks = [
        {"id": "emergency_fund", "passed": months is not None and months >= EMERGENCY_MONTHS,
            "facts": {"months_covered": months, "target_months": EMERGENCY_MONTHS, "essentials_per_month": essentials, "cash": cash},
            "why": "An emergency fund means a surprise bill never forces you to sell investments at a bad time."},
        {"id": "costly_debt", "passed": not costly, "facts": {"debts": [d["name"] for d in costly], "threshold_percent": HIGH_INTEREST},
            "why": "Paying off debt above about 12% a year is a guaranteed return that investments rarely beat."},
        {"id": "surplus", "passed": surplus is not None and surplus > MIN_SIP,
            "facts": {"surplus_per_month": surplus, "pace_per_month": pace, "goal_needs_per_month": goal_need},
            "why": "Investing should come from money left after your goals, not from money they need."},
    ]
    ready = all(c["passed"] for c in checks)
    sip = max(int(surplus * SIP_SHARE) // 50_000 * 50_000, MIN_SIP) if ready else None
    first_fix = next((c["id"] for c in checks if not c["passed"]), None)
    return {"ready": ready, "status": "ready" if ready else "not_yet", "checks": checks, "sip_minor": sip, "first_fix": first_fix,
        "education": EDUCATION}


def options_for(view, profile):
    """Advisor options from the readiness result: start a SIP, build an emergency fund, or clear costly debt.

    Args:
        view: the dict returned by readiness().
        profile: the owner's saved context.

    Returns:
        List of advisor option dicts (ref, kind, summary, facts).

    Used by: GET /api/v1/invest and POST /api/v1/invest/explain. Seen as the advice list on the Invest page.
    """
    checks = {c["id"]: c for c in view["checks"]}
    out = []
    if view["ready"]:
        out.append({"ref": "i1", "kind": "start_sip", "summary": "Start a small monthly SIP",
            "facts": {"sip_per_month": view["sip_minor"], "surplus_per_month": checks["surplus"]["facts"]["surplus_per_month"]}})
    ef = checks["emergency_fund"]["facts"]
    if not checks["emergency_fund"]["passed"] and ef["essentials_per_month"]:
        need = ef["essentials_per_month"] * EMERGENCY_MONTHS - (ef["cash"] or 0)
        if need > 0:
            out.append({"ref": "i2", "kind": "build_emergency_fund", "summary": "Build a three-month emergency fund first",
                "facts": {"essentials_per_month": ef["essentials_per_month"], "fund_target": ef["essentials_per_month"] * EMERGENCY_MONTHS,
                    "still_needed": need}})
    if not checks["costly_debt"]["passed"]:
        out.append({"ref": "i3", "kind": "clear_costly_debt", "summary": "Clear high-interest debt before investing",
            "facts": {"debt_name": checks["costly_debt"]["facts"]["debts"][0], "threshold_percent": HIGH_INTEREST}})
    return out


def headlines(feeds, limit=8, ttl=900):
    """Recent market headlines from public RSS feeds, cached; titles, sources and links only.

    HTTPS feeds only, size-limited, tip-style headlines ("stocks to buy", "target price") removed. Context, never
    advice.

    Args:
        feeds: list of RSS feed URLs from settings.news_feeds.
        limit: maximum headlines returned (default 8).
        ttl: cache lifetime in seconds (default 900).

    Returns:
        List of {title, link, source, published}.

    Used by: GET /api/v1/invest. Seen as "Market headlines" on the Invest page.
    """
    if not feeds:
        return []
    key = tuple(feeds)
    cached = _news_cache.get(key)
    if cached is not None and time.monotonic() - cached["at"] < ttl:
        return cached["items"]
    items = []
    for url in feeds:
        if not url.startswith("https://"):
            continue
        try:
            response = httpx.get(url, timeout=6, follow_redirects=True, headers={"User-Agent": "FinPilot/0.1 (local)"})
            if response.status_code != 200 or len(response.content) > 2_000_000:
                continue
            root = ElementTree.fromstring(response.content)
        except (httpx.HTTPError, ElementTree.ParseError):
            continue
        source = (root.findtext("channel/title") or httpx.URL(url).host).strip()[:60]
        for item in root.iter("item"):
            title, link = (item.findtext("title") or "").strip(), (item.findtext("link") or "").strip()
            if title and link.startswith("https://") and not TIPS.search(title):
                items.append({"title": re.sub(r"\s+", " ", title)[:200], "link": link, "source": source,
                    "published": (item.findtext("pubDate") or "").strip()[:40]})
            if sum(i["source"] == source for i in items) >= 4:
                break
    items = items[:limit]
    _news_cache[key] = {"items": items, "at": time.monotonic()}
    return items
