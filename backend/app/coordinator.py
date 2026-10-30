"""Coordinator: route a Fina chat message to the agent or service that should handle it.

Deterministic rules handle clear messages without a model call. The model is used only
to classify unclear messages and to extract details the owner stated; every extracted
amount must appear in the owner's own message, dates must fall in the forecast window,
and merchants must appear in the message and in the owner's transactions. Nothing is
saved here: profile and category changes come back as proposals the owner confirms.
"""
import re
from datetime import date
from .mesh import category_adapter

AMOUNT = re.compile(r"(?<![\w.])(?:₹|rs\.?|inr)?\s*(\d[\d,]*(?:\.\d+)?)\s*(k|thousand|lakhs?|lacs?|l|cr|crores?)?\b", re.I)
SCALE = {"k": 1_000, "thousand": 1_000, "l": 100_000, "lakh": 100_000, "lakhs": 100_000, "lac": 100_000,
    "lacs": 100_000, "cr": 10_000_000, "crore": 10_000_000, "crores": 10_000_000}
RULES = [
    ("greeting", r"^\s*(hi|hello|hey|hiya|good (?:morning|afternoon|evening)|thanks|thank you|how are you)\b"),
    ("outlook", r"\b(outlook|if (?:this|it|that) (?:continues|keeps? (?:up|going|rising))|keep (?:spending|going) like this|"
        r"spending (?:trend|pattern|habit)s?|my (?:patterns?|habits?)|on this path|where am i heading)\b"),
    ("unsupported", r"\b(crypto|bitcoin|tax|itr|insurance polic|ipo|option trading|intraday|f&o)\b"),
    ("invest", r"\b(invest(?:ing|ment)?s?|stocks?|shares?|mutual funds?|sip|index funds?|portfolio|markets?|nifty|sensex)\b"),
    ("debts", r"\b(debts?|loans?|emis?|credit cards?|owe|borrow(?:ed|ing)?|interest rates?)\b"),
    ("goal", r"\b(goals?|save up|saving for|save for|target)\b"),
    ("advice", r"\b(what should i do|advice|advise|suggest|recommend|how (?:can|do|should) i (?:save|cut|reduce|spend less))\b"),
    ("setup", r"\b(what'?s missing|what is missing|set ?up|help me (?:start|fill|complete|get started))\b"),
]
EXPLAIN = r"\b(afford|why|balance|forecast|reserve|run out|going down|dropping|enough|tight|this purchase)\b"
INTENTS = {"invest", "debts", "emergency", "outlook", "greeting", "explain", "purchase", "upcoming_flow", "profile_value", "category", "setup", "goal", "advice", "unsupported"}

REPLIES = {
    "setup": "Happy to help you fill in the gaps. I've got a few quick questions that will make your forecast much sharper.",
    "goal": "I love that you're thinking about goals. Tell me what it's for, how much and by when, like \"save 50k "
        "for a trip by March\", or add it on the Goals page and I'll keep track with you.",
    "advice": "I can't give personal recommendations yet; that needs extra checks we're still building. What I can do "
        "is explain your numbers: ask me why your balance is moving, or try a purchase and I'll walk you through it.",
    "unsupported": "That's outside what I can help with. I stick to your own cash: your forecast, upcoming income and "
        "bills, purchases you're weighing, and your categories. Ask me about any of those.",
    "greeting": "Hi! I'm here and happy to help. I can explain your forecast, check a purchase (\"can I afford a 2,000 "
        "dinner on Friday?\"), add upcoming income or bills (\"my salary is 85k on the 1st\"), or learn your categories "
        "(\"Crossword is books\"). What's on your mind?",
    "outlook": "Good question to ask. I keep an eye on spending that's creeping up and what it would mean for your goals "
        "and reserve if it continued. Have a look at your Outlook.",
    "invest": "Happy to look at investing with you. I start with whether it fits your situation right now: an emergency "
        "fund, no costly debt, and money left after your goals. I won't name stocks or funds to buy, but I'll explain your "
        "options and their risks. Have a look at Invest.",
    "debts": "Let's look at what you owe together. The debt agent tracks loans, EMIs, cards and rent, what they cost you each "
        "month, and how to clear them faster. You'll find it on your Outlook.",
    "help": "I can explain your forecast, check a purchase (\"can I afford a 2,000 dinner on Friday?\"), add upcoming "
        "income or bills (\"my salary is 85k on the 1st\"), or learn your categories (\"Crossword is books\").",
}


def amounts_in(message):
    """Every amount the owner wrote, in minor units (handles commas, k, thousand, lakh, crore).

    Args:
        message: the owner's text.

    Returns:
        Set of amounts in minor units, e.g. {6000000} for "60k".

    Used by: validate(), goal_route(), goal_draft() and flow_draft(), so extracted amounts must have been said.
    """
    found = set()
    for number, unit in AMOUNT.findall(message):
        value = float(number.replace(",", "")) * SCALE.get((unit or "").lower(), 1)
        found.add(round(value * 100))
    return found


def stated_amount(value, stated):
    """Accept a model's amount only if the owner actually wrote it (in minor or major units).

    Args:
        value: amount the model extracted.
        stated: set from amounts_in().

    Returns:
        The amount in minor units, or None when the owner never wrote it.

    Used by: validate().
    """
    if abs(value) in stated:
        return value
    if abs(value) * 100 in stated:
        return value * 100
    return None


def rule_route(message):
    """Route a message by keyword rules before calling the model (greetings, outlook, debts, invest...).

    Args:
        message: the owner's text.

    Returns:
        An intent name, or None to let the model decide.

    Used by: POST /api/v1/assistant/message (Fina chat and voice cards).
    """
    text = message.lower()
    for intent, pattern in RULES:
        if re.search(pattern, text):
            # A goal stated with an amount needs the model to extract name, amount and date.
            return None if intent == "goal" and amounts_in(message) else intent
    if not amounts_in(message) and re.search(EXPLAIN, text):
        return "explain"
    return None


def offline_route(message):
    """A safe route when the model is unavailable: goals to Goals, money questions to the investigator.

    Args:
        message: the owner's text.

    Returns:
        'goal', 'setup' or 'help'.

    Used by: Fina chat when AI is paused or fails.
    """
    if re.search(r"\b(goals?|save|saving|target)\b", message.lower()):
        return "goal"
    if re.search(r"\b(salary|paid|payday|income|rent|emi|loan|bill|subscription|bank|cash|reserve|aside)\b", message.lower()):
        return "setup"
    return "help"


def validate(payload, message, as_of, horizon_end, descriptions):
    """Check the coordinator model's routing and extraction; unsupported or unstated values degrade safely.

    Args:
        payload: the model's parsed routing output.
        message: the owner's text.
        as_of: balance date.
        horizon_end: last forecast date.
        descriptions: (id, description) pairs of the owner's transactions.

    Returns:
        (intent, details) with details checked against the message, or None when nothing usable was extracted.

    Used by: POST /api/v1/assistant/message. Seen as the confirm cards in Fina's chat.
    """
    intent = payload["intent"] if payload["intent"] in INTENTS else "unsupported"
    stated = amounts_in(message)
    in_window = lambda d: d is not None and as_of < d <= horizon_end
    if intent == "purchase":
        p = payload.get("purchase")
        amount = stated_amount(p["amount_minor"], stated) if p else None
        if not amount or amount <= 0:
            return "explain", None
        when = _date(p.get("date"))
        return "purchase", {"amount_minor": amount, "date": when.isoformat() if in_window(when) else None}
    if intent == "upcoming_flow":
        f = payload.get("flow")
        when = _date(f.get("date")) if f else None
        label = (f or {}).get("label", "").strip()
        amount = stated_amount(f["amount_minor"], stated) if f else None
        if not amount or not in_window(when) or not 0 < len(label) <= 80 or re.search(r"[\d<>]", label):
            return "setup", None
        # The owner's words decide the direction when they are clear ("pay 28000 rent" is money out), not the model's sign.
        direction = flow_direction(message)
        if direction:
            amount = abs(amount) if direction == "in" else -abs(amount)
        return "upcoming_flow", {"label": label, "amount_minor": amount, "date": when.isoformat()}
    if intent == "profile_value":
        v = payload.get("profile_value")
        amount = stated_amount(v["amount_minor"], stated) if v else None
        if amount is None or v["field"] not in {"available_cash", "reserve"} or (v["field"] == "reserve" and amount < 0):
            return "setup", None
        return "profile_value", {"field": v["field"], "amount_minor": amount}
    if intent == "category":
        c = payload.get("category")
        merchant = (c or {}).get("merchant", "").strip()
        if not c or len(merchant) < 3 or merchant.lower() not in message.lower():
            return "unsupported", None
        try:
            label = category_adapter.validate_python(c["category"])
        except ValueError:
            return "unsupported", None
        matches = [tx_id for tx_id, text in descriptions if merchant.lower() in text.lower()]
        return "category", {"merchant": merchant, "category": label, "ids": matches}
    if intent == "goal":
        g = payload.get("goal")
        when = _date(g.get("date")) if g else None
        name = (g or {}).get("name", "").strip()
        amount = stated_amount(g["target_minor"], stated) if g else None
        if not amount or amount <= 0 or not when or not as_of < when <= date(as_of.year + 10, 12, 31) \
                or not 0 < len(name) <= 80 or re.search(r"[\d<>]", name):
            return "goal", None
        return "goal", {"name": name, "target_minor": amount, "target_date": when.isoformat()}
    return intent, None


def _date(value):
    """Parse an ISO date, tolerating missing or malformed values.

    Args:
        value: ISO date string or None.

    Returns:
        A date or None.

    Used by: validate().
    """
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


EMERGENCY = re.compile(r"\b(emergency|had to (?:use|take|spend|withdraw)|took (?:out|money from|from) (?:my )?(?:savings|funds|goals?)|"
    r"used (?:my )?(?:savings|goal money))\b", re.I)
SAVING = re.compile(r"\b(saved|save|saving|set aside|put|putting|added|add|deposit(?:ed)?|moved|transferred)\b", re.I)


def goal_route(message, goals):
    """Goal-aware routing from the owner's own goal names and phrases; no model needed.

    Args:
        message: the owner's text.
        goals: the owner's active goals.

    Returns:
        (intent, details) for emergency, goal_saving, goal_status or goal_saving_ask, or None.

    Used by: Fina chat: "I saved 5000 for Goa", "how is my goal doing", "I had an emergency".
    """
    text = message.lower()
    if EMERGENCY.search(text):
        amounts = sorted(amounts_in(message), reverse=True)
        return "emergency", {"amount_minor": amounts[0] if amounts else None}
    named = [g for g in goals if g["name"].lower() in text
        or all(w in text for w in re.findall(r"[a-z]{3,}", g["name"].lower()) or [g["name"].lower()])]
    amounts = sorted(amounts_in(message), reverse=True)
    saving = SAVING.search(message)
    if saving and amounts and (named or len(goals) == 1):
        goal = (named or goals)[0]
        return "goal_saving", {"goal": goal, "add_minor": amounts[0]}
    if named:
        return "goal_status", {"goals": named}
    if saving and goals and not amounts:
        return "goal_saving_ask", {"goals": goals}
    if goals and re.search(r"\b(my goals?|goal progress|how am i doing|on track)\b", text):
        return "goal_status", {"goals": goals}
    return None


MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august", "september",
    "october", "november", "december"], 1)} | {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug",
    "sep", "oct", "nov", "dec"], 1)} | {"sept": 9}
FILLER = {"save", "saving", "saved", "savings", "for", "by", "a", "an", "the", "to", "goal", "goals", "rupees", "rupee", "rs", "inr",
    "k", "thousand", "lakh", "lakhs", "lac", "crore", "i", "want", "wanna", "need", "my", "of", "next", "this", "year", "years",
    "month", "months", "in", "within", "put", "set", "aside", "up", "buy", "get", "add", "create", "make", "please", "and",
    "around", "about", "till", "until", "before", "end", "on", "at", "me", "help", "plan", "planning", "trip"} | set(MONTHS)


def _end_of_month(year, month):
    """The last day of a month.

    Args:
        year: year.
        month: month 1-12.

    Returns:
        A date.

    Used by: goal_draft().
    """
    first_of_next = date(year + month // 12, month % 12 + 1, 1)
    return date.fromordinal(first_of_next.toordinal() - 1)


def goal_draft(message, as_of):
    """Best-effort goal fields from a spoken or typed sentence on the Goals page, for the owner to check.

    Args:
        message: what the owner said, e.g. "save 60,000 for a Goa trip by March" or "6004 Goa".
        as_of: balance date (date).

    Returns:
        Dict with name, target_minor and target_date (each None when not said).

    Used by: Fina's chat with page='goals'. Seen as the prefilled goal card under "Tell Fina about a goal".
    """
    text = message.lower()
    amounts = sorted(amounts_in(message), reverse=True)
    when = None
    if m := re.search(r"\b(?:in|within)\s+(\d{1,2})\s+(months?|years?)\b", text):
        months = int(m.group(1)) * (12 if m.group(2).startswith("year") else 1)
        when = _end_of_month(as_of.year + (as_of.month - 1 + months) // 12, (as_of.month - 1 + months) % 12 + 1)
    elif m := re.search(r"\b(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\b(?:\s+(20\d\d))?", text):
        month, year = MONTHS[m.group(1)], int(m.group(2)) if m.group(2) else as_of.year
        when = _end_of_month(year, month)
        while when <= as_of:
            when = _end_of_month(when.year + 1, month)
    elif re.search(r"\bnext year\b", text):
        when = date(as_of.year + 1, 12, 31)
    words = re.findall(r"[a-z]+", re.sub(r"\d[\d,.]*\s*(k|thousand|lakhs?|lacs?|crores?|cr|l)?\b", " ", text))
    kept = [w for w in words if w not in FILLER or (w == "trip" and words.index(w) > 0)]
    name = " ".join(kept).strip()
    if "trip" in words and "trip" not in kept:
        name = f"{name} trip".strip()
    name = name[:1].upper() + name[1:] if name else ""
    return {"name": name[:80] or None, "target_minor": amounts[0] if amounts else None,
        "target_date": when.isoformat() if when and when <= date(as_of.year + 10, 12, 31) else None}


MONEY_IN = re.compile(r"\b(salary|pay ?day|paycheck|freelance|fee|fees|bonus|income|refund|cashback|interest|dividend|"
    r"stipend|commission|client|invoice|received|credit(?:ed)?|rent (?:from|received)|allowance)\b", re.I)
FLOW_FILLER = FILLER - {"rent", "fee", "fees", "salary", "bonus", "bill", "trip"} | {"comes", "coming", "come", "paid", "pay", "pays",
    "due", "every", "monthly", "each", "is", "will", "be", "should", "get", "gets", "getting", "receive", "st", "nd", "rd", "th",
    "day", "date", "my", "our", "account", "bank", "of", "rupees"}


def flow_draft(message, as_of):
    """Best-effort upcoming money in or out from a sentence on Your context, for the owner to check.

    Args:
        message: what the owner said, e.g. "freelance fee" or "rent 28000 on the 2nd".
        as_of: balance date (date).

    Returns:
        ('profile_value', {field, amount_minor}) for cash or reserve, else ('upcoming_flow', {label, amount_minor,
        date, direction}).

    Used by: Fina's chat with page='context'. Seen as the prefilled card under "Tell Fina instead of typing".
    """
    text = message.lower()
    amounts = sorted(amounts_in(message), reverse=True)
    amount = amounts[0] if amounts else None
    if amount and re.search(r"\b(reserve|buffer|keep aside|safety net|cushion)\b", text):
        return "profile_value", {"field": "reserve", "amount_minor": amount}
    if amount and re.search(r"\b(i have|i've got|balance|in (?:my|the) (?:account|bank)|cash in hand|available)\b", text):
        return "profile_value", {"field": "available_cash", "amount_minor": amount}
    when = None
    if m := re.search(r"\b(?:on\s+(?:the\s+)?)?(\d{1,2})(?:st|nd|rd|th)\b", text):
        day = int(m.group(1))
        if 1 <= day <= 31:
            year, month = as_of.year, as_of.month
            for _ in range(2):
                try:
                    candidate = date(year, month, day)
                except ValueError:
                    candidate = None
                if candidate and candidate > as_of:
                    when = candidate
                    break
                year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    elif m := re.search(r"\b(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\b(?:\s+(20\d\d))?", text):
        month, year = MONTHS[m.group(1)], int(m.group(2)) if m.group(2) else as_of.year
        when = date(year, month, 1)
        while when <= as_of:
            when = date(when.year + 1, month, 1)
    words = re.findall(r"[a-z]+", re.sub(r"\d[\d,.]*\s*(k|thousand|lakhs?|lacs?|crores?|cr|l|st|nd|rd|th)?\b", " ", text))
    label = " ".join(w for w in words if w not in FLOW_FILLER).strip()
    label = (label[:1].upper() + label[1:])[:80] if label else None
    incoming = bool(MONEY_IN.search(text)) and not re.search(r"\b(pay|paid|bill|emi|rent due|fee due)\b", text.replace("pay day", "payday"))
    return "upcoming_flow", {"label": label, "amount_minor": (amount if incoming else -amount) if amount else None,
        "date": when.isoformat() if when else None, "direction": "in" if incoming else "out"}


MONEY_OUT = re.compile(r"\b(pay|paying|paid|bill|bills|emi|emis|rent|loan|premium|insurance|subscription|due|owe)\b", re.I)


def flow_direction(message):
    """Whether the owner's words say money is coming in or going out, or None when they are mixed or silent.

    Args:
        message: what the owner typed or said, e.g. "I have to pay 28000 rent" or "my salary comes on the 1st".

    Returns:
        'in', 'out' or None. "Rent from my tenant" and "rent received" count as money in.

    Used by: validate() to correct the model's sign on upcoming payments, and the supervisor's card check.
    """
    text = message.lower().replace("pay day", "payday")
    incoming = bool(MONEY_IN.search(text))
    outgoing = bool(MONEY_OUT.search(re.sub(r"\brent (?:from|received)\b", " ", text)))
    if incoming and not outgoing:
        return "in"
    if outgoing and not incoming:
        return "out"
    return None
