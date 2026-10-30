# Iteration 10: goals and the goal tracker agent

Date: 2026-10-26. Author: Claude Code. Request: second of the three agents the user asked
for (coordinator, goals, advisor). The original brief asks for "goals to be achieved
checklist with dates".

## Previous state and change

Goals did not exist; the goal tracker had a v1 prompt only and chat replied that goals
were coming. Affected requirement: the goals part of FP-02 and the brief's goal
checklist, partially. FinaCoins, referrals and reminders are not included.

- **Data.** A tenant-scoped `goals` table (name, target amount, target date, amount set
  aside, status `active` or `achieved`), under the same PostgreSQL row-level security
  and grants as other owner tables; the startup check now covers it. Up to 20 goals per
  owner; target dates must be after the balance date.
- **Progress (deterministic).** Percent set aside, amount remaining, months left, the
  monthly amount needed to reach the target on time, and the owner's monthly pace from
  the latest fresh forecast (net change over the horizon scaled to a month). Status is
  `reached`, `past_due`, `needs_forecast`, `on_track` (pace covers the need), `stretch`
  (positive pace but short) or `off_track` (balance not growing). Progress is never
  stored, so it always reflects current inputs.
- **Goal tracker agent.** "Ask Fina" on a goal returns a two-to-three sentence note in
  Fina's voice from goals v2. Every amount, date, count and percentage is a placeholder
  filled by the server from the calculation; notes with digits, unknown placeholders,
  promises, or coins and rewards are replaced by a calculated note in the same voice.
- **Goals page.** A checklist: tick to mark achieved, progress bar, monthly need and
  pace, an editable "set aside so far" amount, delete, and an add form. Agent activity
  shows the goal tracker as an active stage.
- **Chat.** Coordinator v3 extracts goals ("I want to save 50k for a new laptop by
  March") into an "Add goal" card the owner can edit and confirm; the amount must appear
  in the message and the date must be in the future. Goal wording without an amount,
  or without AI, links to the Goals page.

## Acceptance and evidence

- Tests: progress status for on track, stretch, off track, needs forecast, reached and
  past due with exact figures; create, list, update, achieve and delete with another
  owner getting 404 and seeing nothing; date and amount validation; the calculated note
  text; model notes shown only when grounded (digits, coins and unknown placeholders
  fall back); a chat goal statement producing a confirm card without saving. Backend
  suite on 2026-10-26: 103 passed, 3 PostgreSQL tests skipped. Thirteen prompt releases
  validate. Frontend build passed.
- Browser (demo account, AI on): a ₹60,000 goal by 28 Feb 2027 with ₹15,000 set aside
  showed 25%, ₹45,000 to go, about ₹10,299 a month needed against a ₹47,943 monthly pace,
  "On track"; Fina's model note used only those figures; ticking it showed "Reached"
  immediately; a chat goal card added "Laptop, 50,000, 31 Mar 2027" and the page
  updated without reloading. Test goals were removed afterwards.

## Data, security and evaluation impact

New `goals` table; SQLite creates it on startup and PostgreSQL through `init-db` with
RLS. Goals and notes stay within the owner's tenant; operations sees only
`goal_create`, `goal_update`, `goal_delete` and `goal_coach` activity. The pace is a
simple projection from a 30–90 day forecast and includes one-off scheduled flows in that
window, so it can overstate a typical month. Goals v2 and coordinator v3 (release v13)
are draft with evaluation NOT RUN.
