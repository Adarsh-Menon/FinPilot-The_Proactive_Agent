# Iteration 13: goal suggestions — subscriptions and spending cuts toward a goal

Date: 2026-10-27. Author: Claude Code. Request: the user expected the advisor to suggest,
on the Goals page, useful ways to reach a goal, such as reducing a subscription or food
spending.

## Previous state and change

The advisor (iteration 11) only appeared when a what-if dipped under the reserve or in
chat, and its only goal option was a later target date. It never looked at subscriptions.

- **Goal options (calculated).** For each active goal that is not reached or past due:
  - *Pause a subscription*: repeating monthly charges with a steady amount (within 5%)
    over the last four months, outside essentials, detected from the owner's history.
  - *Spend a quarter less* on the two largest non-essential categories over the history
    period, leaving out subscription charges so the same money is not counted twice and
    owner-marked recurring bills.
  - Each option states the monthly saving, the yearly total, the share of the goal's
    monthly need it covers and, only when the goal would be reached at least a month
    sooner, the new date and months saved. Without a growing balance in the forecast,
    the date is what the saving alone would achieve. Essentials are never offered.
- **Explanation and review.** Advisor v5 explains the options for a goal in Fina's voice
  ("pause a subscription you may not need", never assuming it is unused), with numbers
  as server-filled placeholders; risk reviewer v4 explicitly allows pausing a
  non-essential subscription. The same grounding checks, risk approval, calculated
  fallback and pause switch as iteration 11 apply.
- **Goals page.** Each goal card has "Ways to get there sooner", showing up to three
  options. `POST /api/v1/goals/{id}/advice`; admin activity records `goal_advice`.

Issues found while building: category cuts double-counted a subscription (Netflix was
also the whole "entertainment" category), so subscription charges are excluded from
cuts; an on-track goal was told it would be reached "13 Dec instead of 14 Dec", so dates
appear only when they move by at least a month and the yearly saving was added; and
percentages read "3% percent", so they are now filled in as bare numbers.

## Acceptance and evidence

- Tests: subscription detection finds the monthly streaming charge but not rent,
  groceries or daily food delivery; option arithmetic (monthly and yearly saving, share
  of need, months sooner, date only when at least a month sooner, saving-only date
  without a forecast) checked independently; no options for a reached goal; paused
  policy; calculated wording; a model answer rendered only after risk approval, with no
  transaction descriptions sent. Backend suite on 2026-10-27: 121 passed, 3 PostgreSQL
  tests skipped. Seventeen prompt releases validate. Frontend build passed.
- Browser (synthetic account, AI on): an on-track trip goal and a stretch car goal each
  got two reviewed suggestions (pause Netflix at ₹649 a month; a quarter less on food at
  ₹376 a month) with their share of the goal's monthly need and no misleading dates.

## Data, security and evaluation impact

No schema change. The advisor and reviewer receive category names, subscription names
derived from descriptions (first words, normalized) and calculated amounts; full
descriptions are not sent. Advisor v5 and risk v4 (release v17) are not yet covered by
the iteration 11 evaluation, which measured release v16 on forecast options; goal
options have no evaluation run (NOT RUN).
