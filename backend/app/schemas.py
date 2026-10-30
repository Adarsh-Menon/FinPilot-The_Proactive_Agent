from datetime import date
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

Money = int
Currency = Literal["INR", "AED", "USD", "EUR", "GBP"]
# Category names are created by the LLM or owner; only text shape is constrained.
Category = Annotated[str, StringConstraints(strict=True, strip_whitespace=True,
    to_lower=True, min_length=1, max_length=64, pattern=r"^[^\x00-\x1f\x7f<>]+$"),
    AfterValidator(lambda value: " ".join(value.split()))]


class Strict(BaseModel):
    """Base for all request bodies: unknown fields are rejected."""
    model_config = ConfigDict(extra="forbid")


class Credentials(Strict):
    """Request body for signing in.

    Fields:
        username: 3-40 letters, digits, _ . -
        password: 12-128 characters.

    Used by: POST /api/v1/auth/login and /ops/v1/auth/login (sign-in pages).
    """
    username: str = Field(min_length=3, max_length=40, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=12, max_length=128)


class Registration(Credentials):
    """Request body for creating an account, with an optional referral code.

    Fields:
        referral_code: a friend's code.

    Used by: POST /api/v1/auth/register (Create account).
    """
    referral_code: str | None = Field(default=None, pattern=r"^[A-Za-z0-9]{6,12}$")


AGENTS = Literal["ingestion", "categorizer", "investigator", "analyzer", "forecaster", "numeric_checks", "advisor", "goals",
    "coordinator", "risk"]


class FeedbackInput(Strict):
    """Request body for a thumbs up or down on an agent's output.

    Fields:
        item_ref: which output.
        agent: agent rated.
        surface: where in the app.
        source: 'ai' or 'calculation'.
        rating: 1 or -1.
        reason: reason for a thumbs down.

    Used by: POST /api/v1/feedback (thumbs across the app).
    """
    item_ref: str = Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")
    agent: AGENTS
    surface: Literal["agent_activity", "chat", "advice", "goal_note", "goal_ideas", "outlook", "investigator", "category"]
    source: Literal["ai", "calculation"] | None = None
    rating: Literal[1, -1]
    reason: Literal["wrong_numbers", "not_relevant", "confusing", "tone", "too_slow", "other"] | None = None


class DebtInput(Strict):
    """Request body for tracking a debt found in the owner's history.

    Fields:
        name: debt name.
        kind: loan, credit_card, rent or other.
        balance_minor: amount still owed.
        rate_percent: yearly interest rate.
        monthly_minor: monthly payment.

    Used by: POST /api/v1/debts (entering owed and rate on a detected row).
    """
    name: str = Field(min_length=1, max_length=80, pattern=r"^[^<>]+$")
    kind: Literal["loan", "credit_card", "rent", "other"]
    balance_minor: int = Field(strict=True, ge=0, le=10**12)
    rate_percent: float = Field(ge=0, le=60)
    monthly_minor: int = Field(strict=True, gt=0, le=10**12)


class DebtPatch(Strict):
    # Only what the owner knows better than the ledger; name, type and monthly payment come from history.
    """Request body for changing only the amount owed and the yearly rate of a tracked debt.

    Fields:
        balance_minor: amount still owed.
        rate_percent: yearly interest rate.

    Used by: PATCH /api/v1/debts/{id} (inline fields on Outlook > Debts).
    """
    balance_minor: int | None = Field(default=None, strict=True, ge=0, le=10**12)
    rate_percent: float | None = Field(default=None, ge=0, le=60)


class Reroute(Strict):
    """Request body for an emergency re-plan.

    Fields:
        amount_minor: emergency amount.
        create_rebuild_goal: also add a goal to rebuild the money.

    Used by: POST /api/v1/goals/reroute(/apply).
    """
    amount_minor: int = Field(strict=True, gt=0, le=10**12)
    create_rebuild_goal: bool = False


class Redeem(Strict):
    """Request body for exchanging FinaPoints for a voucher.

    Fields:
        voucher_id: catalog id.
        idempotency_key: stops a double click redeeming twice.

    Used by: POST /api/v1/rewards/redeem (Referral page).
    """
    voucher_id: str = Field(pattern=r"^[a-z_0-9]{3,40}$")
    idempotency_key: str = Field(min_length=8, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")


class FutureFlow(Strict):
    """One upcoming dated payment in or out.

    Fields:
        date: after the balance date.
        amount_minor: positive in, negative out.
        label: e.g. Salary, Rent.

    Used by: "Upcoming money in & out" on Your context.
    """
    date: date
    amount_minor: int = Field(strict=True, ge=-10**12, le=10**12)
    label: str = Field(min_length=1, max_length=80)


class ProfileInput(Strict):
    """The owner's context: currency, timezone, balance date, history coverage, cash, reserve and flows.

    Used by: PUT /api/v1/profile (Your context).
    """
    currency: Currency = "INR"
    timezone: str = "Asia/Dubai"
    as_of: date
    coverage_start: date
    coverage_confirmed: bool = False
    opening_balance_minor: int | None = Field(default=None, strict=True, ge=-10**12, le=10**12)
    reserve_minor: int | None = Field(default=None, strict=True, ge=0, le=10**12)
    scheduled_flows: list[FutureFlow] = Field(default_factory=list, max_length=200)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        """Reject unknown timezone names.

        Args:
            value: IANA timezone name.

        Returns:
            The value when valid.
        """
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise ValueError("Unknown timezone")
        return value

    @model_validator(mode="after")
    def dates(self):
        """History must start on or before the balance date (at most ten years); flows must be after it.

        Returns:
            The validated profile.
        """
        if not 0 <= (self.as_of - self.coverage_start).days <= 3650:
            raise ValueError("Coverage must start on/before the balance date and span at most ten years")
        if any(flow.date <= self.as_of for flow in self.scheduled_flows):
            raise ValueError("Scheduled flows must be after the balance date")
        return self


class ProfileUpdate(Strict):
    """Request body for saving the profile with the version it was edited from.

    Fields:
        expected_version: rejects saving over newer changes.
        profile: the new profile.
    """
    expected_version: int = Field(ge=0)
    profile: ProfileInput


class TransactionInput(Strict):
    """One transaction; signs must match the kind (expenses and repayments negative, income positive).

    Used by: Manual entry and each CSV row.
    """
    date: date
    description: str = Field(min_length=1, max_length=240)
    amount_minor: int = Field(strict=True, ge=-10**12, le=10**12)
    currency: Currency
    kind: Literal["expense", "income", "transfer", "refund", "borrowing", "repayment"]
    category: Category | None = None
    recurring: bool = False

    @model_validator(mode="after")
    def signs(self):
        """Reject zero amounts and amounts whose sign does not match the kind.

        Returns:
            The validated transaction.
        """
        if self.amount_minor == 0:
            raise ValueError("A transaction must have a nonzero amount")
        if self.kind in {"expense", "repayment"} and self.amount_minor > 0:
            raise ValueError("Expenses and repayments must be negative")
        if self.kind in {"income", "refund", "borrowing"} and self.amount_minor < 0:
            raise ValueError("Income, refunds and borrowing must be positive")
        return self


class ManualInput(Strict):
    """Request body for adding one transaction by hand.

    Fields:
        idempotency_key: stops a retry saving it twice.
        transaction: the transaction.

    Used by: POST /api/v1/transactions (Add transaction).
    """
    idempotency_key: str = Field(min_length=8, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    transaction: TransactionInput


class CategoryUpdate(Strict):
    """Request body for fixing a category: either the owner's own label or a note for the AI.

    Used by: POST /api/v1/transactions/{id}/category (Fix this category dialog).
    """
    expected_version: int = Field(ge=0)
    category: Category | None = None
    note: str | None = Field(default=None, min_length=1, max_length=240)

    @model_validator(mode="after")
    def one_choice(self):
        """Exactly one of category or note must be given.

        Returns:
            The validated body.
        """
        if (self.category is None) == (self.note is None):
            raise ValueError("Provide either a category or a note")
        return self


class Recategorize(Strict):
    """Request body for retrying the AI on uncategorized rows.

    Used by: Retry with AI on Transactions.
    """
    expected_version: int = Field(ge=0)


class CSVInput(Strict):
    """Request body for previewing a CSV import.

    Fields:
        csv_text: file contents (up to 500 KB).

    Used by: POST /api/v1/imports (Import transactions).
    """
    csv_text: str = Field(min_length=1, max_length=500_000)


class ConfirmImport(Strict):
    """Request body for confirming an import preview.

    Fields:
        expected_version: owner's data version.
        acknowledge_possible_duplicates: keep rows that overlap existing ones.
    """
    expected_version: int = Field(ge=0)
    acknowledge_possible_duplicates: bool = False


class ForecastInput(Strict):
    """Request body for a forecast.

    Fields:
        horizon_days: 7-90 days.

    Used by: Calculate forecast on Overview.
    """
    horizon_days: int = Field(default=30, ge=7, le=90)


class ScenarioInput(Strict):
    """Request body for a what-if purchase against a forecast.

    Fields:
        baseline_id: forecast run id.
        amount_minor: purchase amount.
        currency: currency.
        date: purchase date.

    Used by: What if? on Overview.
    """
    baseline_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    amount_minor: int = Field(strict=True, gt=0, le=10**12)
    currency: Currency
    date: date
    funding_account: Literal["primary"] = "primary"


class ChatTurn(Strict):
    """One earlier turn of a Fina conversation.

    Fields:
        role: 'owner' or 'assistant'.
        text: the message.
    """
    role: Literal["owner", "assistant"]
    text: str = Field(min_length=1, max_length=1200)


class ScenarioQuestion(Strict):
    """A purchase being discussed with Fina.

    Fields:
        amount_minor: amount.
        date: date.
    """
    amount_minor: int = Field(strict=True, gt=0, le=10**12)
    date: date


class AssistantInput(Strict):
    """Request body for asking Fina about a forecast (older explain endpoint)."""
    baseline_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    question: str = Field(min_length=1, max_length=500)
    scenario: ScenarioQuestion | None = None
    history: list[ChatTurn] = Field(default_factory=list, max_length=6)


class VoiceInput(Strict):
    # 16 kHz mono 16-bit WAV, capped at about 25 seconds by the recorder; the limit leaves headroom.
    """Request body for server speech to text.

    Fields:
        audio_base64: 16 kHz mono WAV, base64.

    Used by: POST /api/v1/voice/transcribe (mic buttons when a speech model is configured).
    """
    audio_base64: str = Field(min_length=100, max_length=1_200_000, pattern=r"^[A-Za-z0-9+/=]+$")


class ChatMessage(Strict):
    """Request body for a Fina chat message.

    Fields:
        question: what the owner typed or said.
        baseline_id: forecast run to talk about.
        scenario: purchase being discussed.
        history: last few turns.
        page: 'goals' or 'context' when sent from those voice cards.

    Used by: POST /api/v1/assistant/message (Fina chat and the voice cards).
    """
    question: str = Field(min_length=1, max_length=500)
    baseline_id: str | None = Field(default=None, pattern=r"^[0-9a-f-]{36}$")
    scenario: ScenarioQuestion | None = None
    history: list[ChatTurn] = Field(default_factory=list, max_length=6)
    # Where the message was sent from; on Goals, anything that isn't about an existing goal is read as a new one.
    page: Literal["goals", "context"] | None = None


GoalName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80, pattern=r"^[^\x00-\x1f\x7f<>]+$")]


class GoalInput(Strict):
    """Request body for creating a goal.

    Used by: POST /api/v1/goals (Add a goal, Fina, voice).
    """
    name: GoalName
    target_minor: int = Field(strict=True, gt=0, le=10**12)
    target_date: date
    saved_minor: int = Field(default=0, strict=True, ge=0, le=10**12)


class GoalPatch(Strict):
    """Request body for updating a goal (name, target, date, amount set aside or ticking it done).

    Used by: PATCH /api/v1/goals/{id} (goal cards).
    """
    name: GoalName | None = None
    target_minor: int | None = Field(default=None, strict=True, gt=0, le=10**12)
    target_date: date | None = None
    saved_minor: int | None = Field(default=None, strict=True, ge=0, le=10**12)
    status: Literal["active", "achieved"] | None = None


class PolicyInput(Strict):
    """Request body for changing the AI policy.

    Fields:
        expected_revision: rejects stale changes.
        mode: RUNNING, ADVICE_PAUSED or AI_PAUSED.
        reason: why (kept in the audit log).

    Used by: PUT /ops/v1/policy (Admin > Overview).
    """
    expected_revision: int = Field(ge=1)
    mode: Literal["RUNNING", "ADVICE_PAUSED", "AI_PAUSED"]
    reason: str = Field(min_length=5, max_length=200)


class BudgetInput(Strict):
    """Request body for setting the daily AI budget.

    Fields:
        daily_budget_usd: cap per day, or None to remove it.
        auto_pause: pause AI at the cap.

    Used by: PUT /ops/v1/budget (Admin > AI costs).
    """
    daily_budget_usd: float | None = Field(default=None, gt=0, le=10_000)    # None removes the cap
    auto_pause: bool = True


class SuspendInput(Strict):
    """Request body for suspending a customer.

    Fields:
        actor: pseudonym u-xxxxxx.
        reason: why.

    Used by: POST /ops/v1/suspensions (Admin > Abuse & limits).
    """
    actor: str = Field(pattern=r"^u-[0-9a-f]{6}$")
    reason: str = Field(min_length=5, max_length=200, pattern=r"^[^<>]+$")


EvalSuite = Literal["coordinator", "categorizer", "forecaster", "goals_coach", "risk", "system"]


class EvalRunInput(Strict):
    """Request body for starting an evaluation run from the admin portal.

    Fields:
        suites: which agent suites to run (fixed names, never free text).
        split: which golden cases: 'dev', 'holdout' or 'all'.
        judge: also score with the LLM judge (more model calls).

    Used by: POST /ops/v1/evals/run (Admin > Evaluations > Run evaluation).
    """
    suites: list[EvalSuite] = Field(min_length=1, max_length=6)
    split: Literal["dev", "holdout", "all"] = "dev"
    judge: bool = True


class PreferencesInput(Strict):
    """Request body for the owner's sharing choices on the Preferences page.

    Fields:
        share_categories: share confirmed category labels as categorizer examples.
        share_questions: share questions to Fina and how they were routed as coordinator examples.

    Used by: PUT /api/v1/preferences.
    """
    share_categories: bool = False
    share_questions: bool = False
