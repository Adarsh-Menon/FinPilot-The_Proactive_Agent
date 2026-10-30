# Iteration 17: Voice, proven FinaPoints, celebrations, debts and the new brand

Date: 2026-10-27. Author: Claude Code. Requests from the user: speak to Fina and have
Fina speak back, including on Goals and Your context, so telling Fina is an alternative to
typing; give Fina a profile picture; a fix for a recovery route that promised a goal
"by 2030-10-11"; FinaPoints only when later transactions prove the saving, never negative and
never taken back; a pop-up with effects when something is completed; debts where only the
amount owed and the rate are typed in; and the FinPilot logo, admin header and loader.

## Recovery dates when the balance is shrinking

The "move the date" route clamped a negative monthly pace to zero and then counted the
owner's subscription cuts (₹649 + ₹299 = ₹948) as the whole saving rate, so a goal with
₹45,000 to go and a balance falling ₹3,142 a month was shown as reachable in four years.
Now the real pace is kept:

- If pace plus cuts is still zero or negative, there is no date. A **Stop the balance
  shrinking first** route shows the monthly fall, what the cuts would save and what is
  still short, because any date would be made up.
- If a date exists but is more than two years past the target, the route says so and points
  to the catch-up or a smaller milestone instead of presenting waiting as a plan.

## Voice

Voice uses the browser's Web Speech API (Chrome and Edge; other browsers hide the controls).
Speech is recognised as Indian English and replies use an Indian English voice when one is
installed. Amounts are read as "35,000 rupees" and dates as "7 November 2026".

- **Fina chat:** a mic beside the input. A spoken question is sent when you stop talking
  and Fina reads the reply aloud. Typed questions stay silent. Every reply has **Listen**,
  and the speaker in the header mutes Fina; that choice is remembered in this browser.
- **Goals and Your context:** a "Tell Fina" voice card. What you say goes through the same
  Fina routing and checks as chat, and comes back as the same editable confirm card ("Add
  goal", "Add to my plan", "Add to …"). Nothing is saved until you confirm.

Privacy: in Chrome and Edge, recognition runs on the browser vendor's speech service, so
the audio of what you say leaves the device to Google or Microsoft, not to FinPilot. Only
the recognised text reaches FinPilot, exactly as if it were typed. Speech output is local.

## FinaPoints are earned with proof, never taken back

- A ticked goal earns its 1,000 points only when **transfers into savings made on or after the
  day the goal was created** cover the full target. Savings transfers are transfers whose
  description or category reads like savings, an FD, RD, SIP, mutual fund, PPF, NPS, gold or a
  savings jar. Money moved back out comes off the latest deposits first.
- Each rupee proves one goal: goals already rewarded keep first claim, then goals in the order
  they were created. Proof is re-checked whenever Goals or Referral load, so importing a
  statement later is enough; the goal card shows what was found since the goal was set.
- No punishment: there are no reversals, un-ticking keeps the points, and the balance never
  goes below zero. Older entries made under the previous rules (unproven goal points and their
  reversals) no longer count, so "Earned so far" shows only proven goals, friends and
  achievements.
- Limits: matching is by wording on one consolidated account, so a transfer described only as
  "To Rahul" does not count, and a self-described "savings" transfer is trusted.

## Celebrations

Any response that completes a goal or unlocks an achievement (goals, rewards, debts, invest,
redeem) carries `unlocked`; the app shows each one once as a pop-up: a 3D token dropping in with
a spin, light rays, sparks, confetti and the points earned. Enter or Escape closes it; reduced
motion turns the effects off.

## Debts: only owed and rate

The add-a-debt form is gone. Debts come from the owner's history (EMIs, loan payments, rent);
each row has inline fields for the amount still owed and the yearly rate, and name, type and
monthly payment come from the transactions. A `PATCH /api/v1/debts/{id}` accepts only those two
fields. Clearing a debt to zero unlocks Debt Slayer.

## Categorizer: one label teaches every matching transaction

- When the owner sets a category, or the AI categorizes a transaction from their note, every
  other transaction from the same merchant that the owner has not labelled themselves gets it
  too ("Matches your label for “zomato”"), and later imports and entries follow it without a
  model call. If the owner's note names the merchant ("zomato is food delivery") the rule covers
  every row with that word; otherwise a row must contain all the label's distinctive words
  after bank noise (UPI, POS, NEFT, bank codes, handles, references), so "Zomato gift card"
  covers only gift cards. When labels disagree the most specific wins, and a tie goes to the
  AI. Only rows of the same kind change; the owner's own labels are never overwritten.
- The per-transaction limit of three AI attempts is removed. Instead each customer has **20
  AI categorization requests a day** (notes and retries), counted from the activity log;
  setting a category yourself is never limited.
- Imports send each distinct merchant line once (reference numbers are already redacted, so
  repeated UPI lines collapse) and run batches side by side. The 222-row messy sample went from
  106 seconds to 26 seconds with all 76 uncategorized rows labelled. The import dialog says it
  is working while this runs. UPI handles are redacted without swallowing the whole line.
- `samples/transactions-sample-v2.csv`: a messy synthetic statement for 19 Oct to 31 Dec 2026
  (UPI/POS/NEFT strings, blank and inconsistent categories, a double charge and reversal, split
  rent, savings in and out, a December food-delivery spike).

## Overview: where your money goes

A panel on Overview shows spending by category for the last 30, 60 or 90 days or all history
up to the balance date: tiles for the most expensive and least expensive category (amount,
share of spending, transaction count) and the total, then a sorted bar per category (one hue,
uncategorized and an "Other" fold beyond eight categories in grey). Hovering or focusing a bar
shows its share, count and average amount. Only expenses count; transfers, refunds and loan
repayments are left out. Calculated in the browser from the owner's own transactions.
Clicking a category (or Enter on it) opens Transactions filtered to that category's spending
for the same period, with a chip showing the count and total and a Show all button; leaving
Transactions clears the filter.

## Server speech to text (optional)

The provider's speech-model setting (`MESH_SPEECH_MODEL` for Mesh) names an audio-input model. When set and AI is
running, the mic records up to 20 seconds (stopping after a pause), converts it to 16 kHz WAV in
the browser and the server sends it to that model, with a prompt that returns only the words and
ignores instructions in the audio; nothing is stored. If it fails, the next tap uses the
browser's recognition. The requested free model was refused by the provider (free tier limited
to listed agent apps), so the setting is empty and voice uses the browser until a paid model is
chosen. Up to 40 clips an hour per customer.

## Brand and loader

The FinPilot mark (mint circle, rising line, dot and ring) replaces the compass in the sidebar,
sign-in, admin header and browser tab. Every loading state uses the mark animated: the line
draws itself and the dot and ring pop in.

## Fina's avatar

Fina's avatar is a dark green orb (`frontend/public/fina-avatar.svg`) wherever Fina
appears. A `frontend/public/fina.png`, if present, is shown in its place without a code
change.

## Acceptance and evidence

- Tests: recovery routes for a shrinking balance return the shortfall route and no date; a
  slow pace flags a far date; savings deposits and withdrawals, proof only after the goal and
  one rupee per goal; points never negative and legacy reversals ignored; a goal pays only after
  proof, once, and keeps its points when un-ticked or when money leaves savings; debts accept
  only owed and rate; voice rejects non-WAV input and sends WAV to the speech model. Backend
  suite: 149 passed, 3 PostgreSQL tests skipped. Frontend build passed.
- Browser (synthetic account): a transfer to savings dated after the House deposit goal opened
  a "House deposit complete, +1,000 FinaPoints" pop-up with confetti; Referral showed 0 points
  instead of a negative balance; debts showed inline owed and rate fields; the loader, sidebar
  and admin sign-in use the new mark; Overview showed Groceries as most expensive (₹20,000,
  45%) and Shopping as least (₹6,000) over 30 days, with a working tooltip and period switch
  and no horizontal scroll at phone width.
- Browser (synthetic account, fake speech recogniser because headless Chromium has no
  microphone): saying "save 40,000 for a new phone by June next year" on Goals produced an
  Add goal card for New phone, ₹40,000, 30 June 2027 and Fina spoke the reply; "my salary of
  80,000 comes on the 1st of next month" on Your context produced Salary, ₹80,000, 1 Nov
  2026; a spoken chat question was tagged Spoken and read aloud; after muting, nothing was
  spoken; no page errors. Real microphone recognition was not tested here.
