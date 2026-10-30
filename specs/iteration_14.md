# Iteration 14: behavioural pattern agent and the Outlook page

Date: 2026-10-27. Author: Claude Code. Request: the user asked for an agent and a new
menu that warns "if this continues, you can't fulfil your goals", using the pattern
agent's data, the goal tracker's goals and the advisor's advice, and asked to call the
pattern agent the "behavioural pattern agent".

## Previous state and change

The analyzer compared 30-day spending per category for the Overview's pattern panel but
did not project trends or connect them to goals. The goals page and advisor did not
consider trends. Affected requirements: the analyzer and advisor parts of FP-02, and the
brief's goals checklist.

- **Behavioural pattern agent.** The analyzer is now presented as the behavioural
  pattern agent (same role and prompt lineage; analyzer v2). It finds non-essential
  categories whose spending in the latest 30 days rose by at least 20% and ₹500 over
  the 30 days before; essentials and owner-marked recurring bills are left out, and at
  least 60 days of history are required.
- **If this continues.** The projection assumes each rising category keeps rising by
  the same amount every month until a goal's target date, so its average monthly drag
  is the increase × (months + 1) ÷ 2. A goal gets a warning when its status would get
  worse (on track → a stretch → off track) or its reach date would slip by a month or
  more. A reserve warning appears when continuing the increase within the forecast
  window would breach the reserve, or breach it earlier than today's forecast.
- **Advice from the advisor.** Each warning lists up to three options: first, bringing
  the rising category back to its usual level (saving the increase); then the goal
  options from iteration 13 (pausing subscriptions, trimming other non-essentials),
  without repeating a category that is already a pattern.
- **Outlook page.** A new menu item shows "Patterns to watch", "If this continues…"
  warnings with their advice, a note when patterns do not yet put anything at risk,
  and a history-needed state. Warnings and advice are calculated instantly; "Explain
  with Fina" asks analyzer v2 to describe each warning in Fina's voice with
  server-filled placeholders. An explanation must state its consequence (the goal's new
  status or the reserve breach date), passes the same grounding checks, and is shown
  only after risk review. Chat questions like "what happens if this continues?" link
  to the Outlook. Agent activity names the stage "Behavioural pattern agent".

## Acceptance and evidence

- Tests: only rising non-essentials are detected, with exact amounts and percentage,
  and nothing with under 60 days of history; the projection's pace, status change and
  reserve breach date match an independent calculation; a comfortable goal gets no
  warning; the first advice is bringing the category back to its usual level; the
  endpoint returns calculated text and another customer sees none of it; explanations
  without a consequence or with typed digits fall back; the chat routes trend
  questions. Backend suite on 2026-10-27: 128 passed, 3 PostgreSQL tests skipped.
  Eighteen prompt releases validate. Frontend build passed.
- Browser (synthetic account whose food delivery doubled from ₹9,000 to ₹18,000): the
  Outlook showed the pattern; a house-deposit goal moved from on track to a stretch,
  with the pace falling from ₹53,439 to ₹11,242 a month and the reach date moving from
  7 Apr 2027 to 7 Jan 2029; advice offered bringing food delivery back to ₹9,000 and
  trimming shopping. "Explain with Fina" returned a reviewed explanation in 17 seconds.

## Data, security and evaluation impact

No schema change. The agent and reviewer receive category names, calculated amounts and
dates, never transaction descriptions. Operations sees `outlook` and `outlook_explain`
activity without content. The linear continuation is a deliberate simplification and
can overstate long-range effects; it is labelled a projection in the interface.
Analyzer v2 (release v18) is draft with evaluation NOT RUN.
