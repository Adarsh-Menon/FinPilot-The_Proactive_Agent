# Iteration 6: learning categorizer and forecast chat

Date: 2026-10-26. Author: Claude Code. Requests from the user: (1) the categorizer should
improve recursively with human help: once the owner says a Crossword purchase is books,
the next similar transaction should follow; (2) define how to measure that learning;
(3) add a chat popup in the bottom-right corner where a customer can ask "why can't I
afford this?" and get an explanation.

## 1. Learning from owner corrections

**Previous behavior.** Each categorization used only the transaction text, general
merchant knowledge and the owner's existing category names. Corrections fixed one row
and taught nothing.

**New behavior.** Every categorizer request carries up to 30 `owner_examples` from the
same owner: rows they relabeled or explained with a note first, then their other labels,
one per normalized description. Categorizer v6 uses an example's category when the
merchant or purpose clearly matches, prefers it over general knowledge, says so in the
reason, and does not stretch it to generic words, payment methods or amounts.

This is in-context learning from durable records, not training. Model weights never
change and the model keeps no memory between calls. The owner's ledger is the memory;
correcting a label changes the example used on the next request. There is no
server-side matching rule, which keeps the iteration 3 direction that categories come
from the model. Provenance records how many examples were supplied.

**Limits.** The 30 examples are not chosen by similarity to the new transaction, so a
relevant lesson can be left out once an owner has labeled more than 30 descriptions.
Mistaken owner labels propagate until corrected. Relevance-based retrieval is the next
step if measurement shows the cap matters.

## 2. Measuring the learning (planned, NOT RUN)

Proposed addition to the [evaluation protocol](../evals/README.md). Status: NOT RUN.

- **Paired design.** Run each probe twice, without and with owner examples. The
  difference is the learning; a high score alone could mean the model already knew.
- **Dataset.** About 10 lessons whose owner label differs from the model's default
  (for example "Urban Company" → household help, "Crossword" → self development), each
  with probes: same merchant with different text; same purpose with a different merchant
  (reported separately as a judgment call); lookalikes that must not match ("Crossword
  puzzle app", "Urban Ladder sofa"); and unrelated controls ("Uber ride", a bare UPI
  reference). Roughly 60 probes and 20 controls, synthetic and hand-labeled; the owner
  reviews the labels, since the author must not be the evaluated model.
- **Metrics and proposed gates.** Same-merchant follow rate ≥ 95%; lift over the
  no-example run; lookalike false-match rate ≤ 5%; control change rate ≤ 5%; abstention
  on opaque references kept at 100%; a learning curve after 0, 5, 10 and 20 corrections;
  and a buried-lesson test with 30 examples. Exact match after lowercasing; three
  repeats per condition, reported as mean and range.
- **Production signal.** A content-free counter of how often owners correct a model
  category, shown to operations as a rate over time. Not implemented yet.

Spot check only (not the evaluation): taught "Urban Company" → household help and
"Crossword bookstore" → self development. Afterwards "Urban Company booking" became
household help, "Crossword Phoenix mall" and "Sapna book house" became self development,
while "Uber ride" stayed transport and a bare UPI reference still abstained.

## 3. Forecast chat

**Previous behavior.** No conversational interface; customers read the scenario panel.

**New behavior.** A chat bubble at the bottom-right of every customer page opens a
panel. It explains the current forecast, and the current What if purchase when one has
been simulated, through `POST /api/v1/assistant/explain`.

- **Facts.** The server rebuilds the facts from the stored forecast run and recomputes
  the scenario; client numbers are never trusted. Each fact has a self-describing key
  and a plain-language meaning (for example
  `balance_after_purchase_on_purchase_date`). No transaction descriptions are sent.
- **Forecaster v3** answers in 2–4 sentences and writes every amount, count and date as
  a `{fact_key}` placeholder; the server fills in the exact formatted value. It must not
  give a yes/no affordability verdict, guarantee outcomes, predict untested amounts or
  dates, or give investment, tax or product advice.
- **Server checks.** An answer is rejected if it contains a digit outside a
  placeholder, an unknown placeholder, an affordability verdict or a guarantee.
- **Risk reviewer v2** (the separate risk model) then reviews the rendered text against
  the facts for attribution, verdicts, invention, relevance and advice, and must
  approve every check.
- **Fallback.** Any rejection, failure, pause or missing configuration returns a
  deterministic explanation built from the same facts. The panel labels each answer
  "Checked against your numbers" (model, reviewed) or "Calculated from your forecast".
- **Limits.** 20 questions per customer per hour (single-process); stale forecasts get
  409, other customers' runs 404, out-of-horizon dates 422. A content-free counter,
  `finpilot_assistant_answers_total{source,outcome}`, records shown and rejected answers.

**Fina.** At the user's request the chat is a persona, Fina, a warm personal-finance
expert who speaks in the first person, briefly acknowledges the feeling behind a
question (worry, excitement, frustration), names concerns plainly and never shames.
Forecaster v4 carries the voice for model answers; the calculated fallback is written
in the same voice and adapts to the kind of question (a purchase, a falling balance,
what would help, or a general overview). All earlier rules still apply: no verdict,
no guarantees, placeholders for every number.

**Latency.** The chat model was spending about 450 hidden reasoning tokens per answer,
pushing replies past the 30-second HTTP timeout. Chat explanation calls now ask the
current provider to skip reasoning (no extra parameter is sent to Mesh until its
equivalent is verified); categorization and risk review keep reasoning. The HTTP
timeout is 45 seconds.

**Why placeholders.** The first version let the model write numbers and checked them
afterwards. Live runs showed real but misattributed numbers, for example the forecast's
end balance described as the balance right after paying, and the risk reviewer
approved them. Placeholders make invention and misformatting impossible and make
misattribution require choosing a key whose name contradicts the sentence.

## Decisions and assumptions

Accepted from the user: learning from owner corrections; a bottom-right chat that
explains affordability questions. Engineering decisions awaiting confirmation: no
affordability verdict in chat (consistent with earlier iterations); a model answer is
shown only after server checks and risk approval; otherwise the calculated explanation
is shown; 30 owner examples; 20 questions per hour.

## Acceptance and evidence

- Tests: learning (a corrected description appears as an owner example in the next
  request and drives the result; another customer receives none); chat fallback text
  with exact values; stale, foreign and out-of-range refusals; placeholder filling;
  the forecaster then risk call order; rejection of typed digits, unknown placeholders,
  verdicts and guarantees; risk block fallback; allowed hedged wording; the hourly cap.
  Backend suite on 2026-10-26: 68 passed, 3 PostgreSQL tests skipped. Nine prompt
  releases validate. Frontend production build passed.
- Live chat run (synthetic data, five questions): every response was correct. One
  model answer passed all checks and was shown with correct attribution; two were
  blocked by the risk reviewer, one was rejected for typed digits, and one model call
  failed, so four customers saw the calculated explanation. Latency was 11–37 seconds
  per question: forecaster calls averaged about 16 seconds and risk reviews about 17.
- Live Fina run after the latency change (synthetic data, four questions): forecaster
  replies took 6–9 seconds and full answers including risk review 6–36 seconds. Three
  model answers were shown, warm and correctly attributed; one, which used a category
  amount to stand in for an unentered ₹2,000 purchase, was rejected and replaced by the
  calculated answer asking for the What if panel.
- Headless browser: the chat bubble opens, shows the purchase context, answers a
  suggested question and labels the source, with no page errors.

## Data, security and evaluation impact

No schema migration. Owner examples and chat facts stay within one tenant and are
minimized before inference; telemetry spans carry only agent, model and prompt version.
Prompt releases v6 (categorizer v6), v7–v10 (forecaster v2–v4, risk v2) are draft
with evaluation NOT RUN; release v6's recorded reason mentions iteration 5 because it
was recorded before this split. Chat answer quality, risk-review accuracy and latency
targets are not evaluated. The risk reviewer is now active for chat only; advice
review and the other planned agents remain inactive.
