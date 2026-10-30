# Iteration 11: advisor, risk review of advice, and its evaluation

Date: 2026-10-26. Author: Claude Code. Request: third of the three agents the user asked
for, built together with the advice evaluation the project's plan requires before
advice is served.

## Previous state and change

The advisor had a v1 prompt only; "what should I do?" got a not-yet reply. Affected
requirement: the advice part of FP-02, with FP-18 guardrails and FP-19 measurement for
advice.

- **Options are calculated, not generated.** For a fresh forecast and optional purchase,
  the server computes every option it can stand behind, with exact effects:
  spending a quarter less on each of the two largest discretionary categories (monthly
  spend, monthly saving, new lowest balance, any remaining reserve breach); paying on the
  next income date in the plan; the largest amount that keeps the reserve for the whole
  forecast; adding upcoming income when none is entered; and, for goals that are a
  stretch or off track, the date the current pace can reach. Rent, health, bills, loans,
  insurance, groceries, education and similar essentials are never offered as cuts.
- **Advisor v4 (Fina's voice) chooses and explains** up to three options. Every amount
  and date is a placeholder scoped to its own option and filled by the server. Items are
  rejected for unknown options, digits, another option's facts, verdicts or guarantees,
  and for an option that still dips under the reserve when the text does not say when.
- **Risk reviewer v3 reviews the rendered advice** against the options (attribution, no
  invention, no essentials, no verdict, options match). Only approved advice is shown;
  otherwise the same options are shown in calculated wording. `ADVICE_PAUSED` or
  `AI_PAUSED` serves no advice at all.
- **Where it appears.** A "What could make this work?" button when a what-if dips under
  the reserve or below zero; Fina's chat for advice requests (coordinator route); and an
  "Advisor + risk reviewer" stage in Agent activity. With every planned agent now
  running, the "Coming later" section is gone.

Issues found while building, each fixed: the advisor wrote as the owner ("if I spend
less"), so v3 makes Fina address "you"; the risk reviewer saw options in paise but
advice in rupees and blocked correct advice, so options are now passed in rupees; the
reviewer leaves passing checks unexplained, so an empty reason is accepted; and advice
for "pay later" omitted a remaining reserve breach, which led to the v4 rule and a
deterministic check.

## Evaluation

`evals/advisor_eval.py` generates synthetic personas (balance, optional reserve and
salary, food delivery, groceries, shopping, rent and a subscription, an optional
purchase and goal), runs the deterministic forecast and options, then the live advisor
and risk reviewer, and independently audits everything that would be shown: each rupee
amount and date must equal a formatted fact of the cited option, no essential may be
cut, no verdict or guarantee may appear, and a remaining reserve breach must be stated.

Run on 2026-10-26, seed 7, 100 cases, prompt release v16
([result](../evals/results/advisor-2026-10-26-seed7-n100.json)):

| Metric | Result | Gate |
|---|---|---|
| Fully grounded shown answers | 100% (100 of 100) | ≥ 98% |
| Critical failures | 0 | 0 |
| Advisor output passing grounding checks | 98% | — |
| Risk approval of grounded advice | 93.9% | — |
| Model advice shown (rest: calculated options) | 92% | — |
| Model errors (2 connection drops, 1 malformed reply) | 3 | — |
| Latency, median / 95th percentile | 20.5 s / 39.8 s | not set |

The gate passed. Limits: the personas come from one generator, so this does not show
real-world quality; the audit checks grounding and safety, not usefulness or tone; it
is a single run of a non-deterministic model; there are no human-reviewed gold answers;
and grounding is enforced by construction as well as audited, so the 100% partly
reflects the design rather than model behavior alone (the 98% model grounding rate and
92% shown rate describe the model).

## Acceptance and evidence

- Tests: option arithmetic checked independently, essentials excluded, no complete-plan
  option when income exists; pause serves nothing; calculated fallback order; grounded,
  approved advice rendered exactly with both calls made and no descriptions sent;
  fallback for a risk block, unknown option, digits, cross-option facts, a verdict, and
  hidden reserve breach; chat advice route. Backend suite on 2026-10-26: 114 passed,
  3 PostgreSQL tests skipped. Sixteen prompt releases validate. Frontend build passed.
- Browser (demo account, AI on): a ₹1,60,000 purchase going below zero offered "pay
  after your next income" (stating the balance still dips under the reserve from
  2 Nov 2026) and "spend up to ₹90,775", both risk-reviewed; chat advice showed three
  options; Agent activity listed the stages in order with no planned agents left.

## Data, security and evaluation impact

No schema change. The advisor and reviewer receive only calculated options, category
names and the owner's question; transaction descriptions are not sent. Operations sees
`advice` activity with outcome only. Advisor v4 and risk v3 (release v16) are covered by
the evaluation above; the other prompt releases remain evaluation NOT RUN.
