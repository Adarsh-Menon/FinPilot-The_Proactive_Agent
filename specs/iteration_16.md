# Iteration 16: Recovery routes, debt and investment agents, emergency re-plans and achievements

Date: 2026-10-27. Author: Claude Code. Requests from the user: when a goal falls behind,
the advisor should give a route back; a separate debt agent for loans, rent and other
fixed payments, feeding the advisor and shown on the Outlook with goals it can create;
an investment agent that reads market news and judges whether stocks, mutual funds or a
SIP fit what the owner can afford; re-planning every goal after money is taken out for an
emergency, with new dates; collectible 3D achievement tokens for the first goal, three
goals and so on; reasoning on every recommendation; and Referral last in the navigation.

Decisions accepted by the user: the investment agent covers **readiness and education
only**: it never names a stock, fund or scheme to buy or sell. News comes from **public
RSS feeds**. Referral moves to the end of the sidebar.

## Goals: recovery routes and the full plan

- **Every goal together.** Goals are now also funded in target-date order from the
  current pace (`plans.waterfall`), so each card shows "With all your goals: date"
  alongside its own progress. Funding goals one at a time is more honest than letting
  every goal claim the whole pace.
- **Recovery routes.** For a goal that is a stretch or off track, the Goals page offers
  three calculated routes, each with its reasoning: catch up (the monthly gap, and how
  much of it the owner's own cuts such as subscriptions would cover), move the date (the
  date the current pace reaches the full amount), or a smaller milestone (what the pace
  reaches by the original date). On-track goals get no routes.

## Emergency re-plan

"Had an emergency?" on Goals, or telling Fina "I had to use 50,000 for an emergency",
opens a re-plan. The amount is taken from the goals in proportion to what each holds,
so the setback is shared instead of emptying one goal; anything beyond the goals' savings
comes from cash. Every goal's expected date is re-estimated before and after, and an
optional goal to rebuild the emergency money is added at the end of the queue. The plan
is a preview until the owner applies it. With no active goals, Fina says there is
nothing to re-plan and suggests a rebuild goal.

## Debt agent

The Outlook has a **Debts and fixed payments** section. Owners add loans, credit cards,
rent and other fixed payments (name, balance, yearly rate, monthly payment); the agent
also detects repeating EMIs, loan payments and rent in the ledger. It works out:

- each debt's payoff date and remaining interest from standard amortization (or that the
  payment never clears it), and which debts charge 12% a year or more;
- the share of monthly income going to fixed payments: under 35% comfortable, 35–50%
  elevated, over 50% high;
- an advisor option to pay a little extra on the costliest debt, with the months saved
  and interest saved, and suggested "Pay off …" goals the owner can add in one click.

## Investment agent

A new **Invest** page. The agent checks three things with the numbers behind each: an
emergency fund of three months of essential spending, no debt at 12% a year or more, and
money left each month after what the goals need. When all pass it suggests a cautious
SIP of half that surplus (in ₹500 steps, at least ₹500). When any fails, the advisor
explains what to fix first, and the missing emergency fund can become a goal. Broad
categories (index, short-duration debt, hybrid funds, gold) are explained with horizon
and risk, labelled as education, not recommendations, with a SEBI-registered adviser
disclaimer. **Explain with Fina** adds a model explanation reviewed by the risk model.

Market headlines come from public RSS feeds (`NEWS_FEEDS`), are fetched over HTTPS only,
size-limited, cached for 15 minutes, keep only titles, sources and HTTPS links, and drop
tip-style headlines ("stocks to buy", "target price", "upside"). They are context only
and never feed a recommendation. Fina routes investing questions to the page and declines
crypto, tax, insurance, IPO, F&O and intraday questions.

## Reasoning on every recommendation

Every advisor option now carries a "Why:" line computed from its facts, for example
"Credit card charges 36.0% a year, your highest rate, so each extra rupee there saves the
most interest (₹3,932 in total)". The reasoning is server-written, so it cannot contain
numbers the calculation did not produce. Prompt release v19 selects advisor v6 (new
option kinds; never name securities or predict returns) and risk v5 (approves SIP,
emergency fund and debt options; any named security fails review).

## Achievements

The Referral page shows collectible tokens in bronze, silver, gold and platinum with a
3D shine and tilt: First Summit (first goal, +250 FinaPoints), Hat-trick (three goals,
+500), Goal Getter (five goals, +1,000), Planner (three goals on the go, +100), Better
Together (first friend referred, +100), Treat Yourself (first voucher), Debt Slayer (a
tracked debt cleared, +500) and Ready to Grow (all investment checks passed, +250).
Locked tokens show progress. Each bonus is awarded once through the same idempotent
FinaPoints ledger; a goal counts while its points are not reversed.

## Acceptance and evidence

- Tests: payoff matches a month-by-month amortization and detects payments that never
  clear a debt; debt detection, pressure and costliest-first option; the date-ordered plan
  and the proportional emergency re-plan, including amounts beyond the goals' savings;
  recovery routes only for goals behind; readiness blocks on costly debt, the SIP is half
  the surplus, and no named securities appear; RSS parsing skips unsafe links and tip
  headlines; debt endpoints are owner-scoped; a re-plan preview changes nothing until
  applied; Fina routes emergencies with and without goals and investing questions;
  achievements unlock once with progress. Backend suite on 2026-10-27: 145 passed, 3
  PostgreSQL tests skipped. Frontend build passed.
- Browser (synthetic account): a 36% credit card produced 6% fixed-payment pressure,
  "pay ₹500 more each month clears it 3 months sooner and saves about ₹3,932" with its
  reasoning, and a "Pay off Credit card" goal suggestion; Invest showed "Not quite ready"
  with a failed emergency fund and costly-debt check, matching advice and 8 live
  headlines; Fina turned "I had to use 50000 for an emergency" into a re-plan moving two
  goals' dates (7 Nov to 20 Nov, 11 Dec to 9 Jan) while both stayed on time; First
  Summit and Treat Yourself tokens were unlocked; Referral is last in the navigation;
  no page errors.
- Not done: the advisor evaluation was not rerun for release v19.

## Data, security and evaluation impact

New table `debts` (encrypted per owner, row-level security in the PostgreSQL migration).
News fetching is the first outbound call that is not a model provider; it sends no owner
data. Achievements and goal points remain self-reported and can be gamed, acceptable for
demo points only. Investment content is general education, not personalised investment
advice; naming products would need a registered adviser.
