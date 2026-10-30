# Iteration 8: investigator agent

Date: 2026-10-26. Author: Claude Code. Request: the user asked why the planned agents
were inactive and approved building the investigator next, as the roadmap orders it.

## Previous state and change

The investigator had a v1 prompt but no code path; Fina repeatedly pointed out missing
upcoming income without a way to fix it in context. Affected requirement: FP-05
(investigator), partially.

The investigator now finds what the forecast is missing and asks the owner through Fina:

- **Gaps** (deterministic): no available cash, no upcoming income, no reserve,
  unconfirmed history coverage. Gap questions always use fixed wording; live runs
  showed the model rewording the coverage question into a different question.
- **Regular flows** (model): from the owner's repeated transactions in the last 120
  days, grouped by normalized description (minimized, month names and digits removed),
  investigator v2 proposes the next salary, rent, loan repayment, subscription or similar
  bill with a label, amount and date, citing its group. Refunds, transfers, one-off
  purchases and everyday spending are excluded by prompt and grouping.
- **Grounding.** A model proposal is kept only if it cites a real group, copies one of
  that group's amounts exactly, falls after the balance date and within 90 days, has no
  digits in its question and is not already scheduled. Pattern-based proposals (monthly
  income and owner-marked recurring bills) fill in anything the model skipped, so a
  good suggestion is not lost when the model is inconsistent; they are also the whole
  answer when AI is off or nothing the model proposed survives.
- **Owner confirmation.** The model never writes. The owner accepts, edits or skips
  each suggestion in a "A few questions from Fina" window; confirmed answers are saved
  through the existing profile update with its version check. The window is reachable
  from an Overview banner listing what is missing, from Your context, and from the
  investigator stage now shown in Agent activity. Debts, savings and goals need new
  profile fields and are not asked yet.

`POST /api/v1/investigator/questions` returns the questions and their source and counts
toward the 20-per-hour question cap. Operations sees `investigator` and
`profile_update` activity without content.

## Acceptance and evidence

- Tests: fallback questions cover gaps and monthly patterns and skip everyday spending;
  saved flows are not proposed again; grounded model proposals are kept while digits,
  duplicate and invented refs are dropped; invented amounts or out-of-window dates fall
  back; minimized descriptions reach the model. Backend suite on 2026-10-26: 74 passed,
  3 PostgreSQL tests skipped. Eleven prompt releases validate. Frontend build passed.
- Live model, synthetic history (salary, rent, Netflix, café lunches, Swiggy, a refund):
  about 9 seconds per call; it proposed salary, rent and Netflix and skipped the rest.
  Across runs it once labeled the salary proposal with the wrong kind and once reworded
  the coverage question, which led to the fixed gap wording and pattern fill-in above.
- Browser (synthetic account, AI off): the banner listed four missing facts; the owner
  entered cash, accepted salary and rent suggestions and confirmed coverage; four answers
  were saved, the banner then listed only the reserve, and a new forecast included the
  salary.

## Data, security and evaluation impact

No schema change; answers are ordinary profile fields. The model receives minimized
descriptions, kinds, categories, dates and amounts of the owner's repeated transactions
only. Investigator v2 (release v11) is draft with evaluation NOT RUN; proposal accuracy
across varied ledgers is not measured.
