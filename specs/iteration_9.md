# Iteration 9: coordinator in Fina's chat

Date: 2026-10-26. Author: Claude Code. Request: the user chose to build the remaining
agents in order: coordinator, then goals, then advisor with its evaluation.

## Previous state and change

Fina's chat sent every message to the forecaster explanation. A message such as "my
salary is 85k on the 1st" or "Crossword is books" got a forecast explanation, and "can I
afford a 2,000 dinner?" asked the owner to use the What if panel. Affected requirement:
FP-02 (coordinator and distinct agents), partially.

`POST /api/v1/assistant/message` now routes each message. The chat uses it; the older
`/api/v1/assistant/explain` remains for direct explanations.

| Route | Example | Result |
|---|---|---|
| explain | "why can't I afford this?" | Forecaster explanation with risk review (iteration 6) |
| purchase | "can I afford a 2,000 dinner on Friday?" | Server runs the what-if, then the forecaster explains it; a missing date defaults to the first forecast day and says so |
| upcoming_flow | "my salary is 85k on the 1st" | Confirm card with label, amount and date, editable; saved through the profile only on confirmation |
| profile_value | "I have 52,000 in the bank" | Confirm card for available cash or reserve |
| category | "Crossword is books" | Confirm card listing matching transactions; confirming labels them as the owner, which also teaches the categorizer (iteration 6) |
| setup | "what's missing?" | Opens the investigator's questions (iteration 8) |
| goal, advice | "save for a trip", "what should I do" | Coming-soon replies until iterations 10 and 11 |
| greeting, unsupported, help | "hey Fina", "should I buy crypto?" | Warm greeting, or a reply describing what Fina can do |

**Rules first.** Greetings, investments and tax, goals, advice, setup requests and
number-free forecast questions are routed by fixed patterns without a model call. Other
messages go to coordinator v2, which classifies and extracts details.

**Grounding.** Every extracted amount must equal an amount the owner wrote (commas, "k",
"lakh" and "crore" are understood). Live runs showed the model returning rupees instead
of paise, so a value that matches only after multiplying by 100 is accepted as rupees.
Dates must fall after the balance date and inside the forecast window. A merchant must
appear in the message and in the owner's transaction descriptions. Failed checks
degrade to the setup questions or an explanation; nothing is saved without a confirm
click. Without AI, money statements ("salary", "rent", "bank"…) open the investigator.

## Acceptance and evidence

- Tests: amount parsing; rule routing for nine message types; rejection of invented
  amounts, out-of-window dates and merchants not in the message; rupee-unit acceptance;
  a stated salary producing a proposal without changing the profile; a purchase question
  running the what-if with a stated default date; a category statement finding 60
  matching transactions; offline routing. Backend suite on 2026-10-26: 95 passed,
  3 PostgreSQL tests skipped. Twelve prompt releases validate. Frontend build passed.
- Live model, synthetic data: routing took 3–6 seconds. Salary, rent, bank balance,
  category and dinner messages were routed and extracted correctly once rupee amounts
  were accepted; the dinner question resolved "Friday" to the right date and explained
  the result.
- Browser with AI on (throwaway database): the salary card was saved to the plan, 40
  "Cafe lunch" transactions were labelled "eating out" from one message, "what's
  missing?" offered the investigator, and a greeting got a warm reply; no page errors.

## Data, security and evaluation impact

No schema change. The coordinator sees only the owner's message and recent turns;
merchant matching runs server-side against the owner's own transactions. Confirmations
reuse the existing profile and category endpoints with their version checks. Admin
activity records `fina_message` with the route only. Coordinator v2 (release v12) is
draft; routing accuracy over a varied message set is not evaluated.
