# Iteration 15: Outlook fixes, goal-aware Fina, FinaPoints and feedback

Date: 2026-10-27. Author: Claude Code. Requests from the user: the Outlook looked empty;
a "Referral" tab where reaching goals earns FinaPoints exchangeable for vouchers matched
to spending patterns (1,000 FinaPoints = ₹30); Fina could not keep track of goals; and
thumbs up and down on each step, visible to admins.

## Outlook fixes

The owner's Outlook was empty for two real reasons. New spending (₹14,040 of travel
with nothing the month before) was ignored because the rule needed a prior month; and
the 30-day windows ended at the balance date, nine days after the last import, so recent
spending looked smaller. Windows now end at the latest expense; new spending of at
least ₹1,000 is listed as "new this month" and not projected (it may be one-off); and
when nothing is rising the page shows what was checked. With these fixes the same
account shows food delivery up 52% (16 orders instead of 8). Chat questions mentioning
the Outlook route there.

## Goal-aware Fina

Messages naming one of the owner's goals now get that goal's calculated progress;
"I saved 2,000 for boys trip" (or any saving amount when there is one goal) becomes a
confirm card adding it to the goal; "I have saved" without an amount asks how much and
for which goal. This is rule-based from the owner's own goal names, with no model call.

## FinaPoints and referral

- **Earning.** +1,000 when a goal is reached (ticked achieved with the full amount set
  aside), +500 to the referrer and +250 to the newcomer when someone signs up with a
  referral code. An append-only, encrypted ledger with per-owner idempotency keys
  awards each event once; un-reaching a goal adds a reversal, and reaching it again
  earns again. Balances can go negative after a reversal of spent points, which blocks
  redemption until positive.
- **Redeeming.** ₹30 vouchers for 1,000 points (₹60 food for 2,000), ranked by the
  owner's spending: rising categories from the behavioural pattern agent first, then the
  largest non-essential spend, with the reason shown. Redemption is idempotent per click.
  Vouchers are labelled demos with no monetary value until a funded partner exists.
- **Referral.** Each customer has a random code and invite link (`/?ref=CODE`, which
  opens sign-up with the code filled in). Codes are a separate table that reveals
  nothing about the owner.

## Feedback

Thumbs up and down appear on every agent output: each Agent activity stage (one vote
per stage per forecast), Fina's chat answers (mapped to the agent that answered), advice
lists, goal notes and ideas, Outlook explanations, the investigator's questions and AI
categories. A thumbs down offers fixed reasons only (wrong numbers, not relevant,
confusing, tone, too slow, other); no free text, so nothing personal reaches operations.
One vote per shown output; voting again updates it. The admin portal's Feedback tab
shows per-agent up and down counts, satisfaction (lowest first), AI versus calculated
answers and top reasons, plus recent votes under pseudonymous IDs.

## Acceptance and evidence

- Tests: new spending and window end; goal points once, only when fully saved, reversed
  and re-earned; referral credits both sides once and ignores unknown codes; redemption
  needs points and is idempotent; vouchers follow spending; the user's exact chat
  messages route correctly; feedback is one vote per output, only fixed reasons, admin
  summary and ordering, and customers and operators each kept to their own side.
  Backend suite on 2026-10-27: 136 passed, 3 PostgreSQL tests skipped. Frontend build
  passed.
- Browser (synthetic account): Fina reported the goal's progress, added ₹3,00,000 to it
  from chat, ticking it earned +1,000 FinaPoints, the top voucher was a food voucher
  explained by the 100% rise in food delivery, redemption issued a demo code, all eight
  Agent activity stages showed feedback controls and a vote saved.

## Data, security and evaluation impact

New tables: `rewards` (encrypted, row-level security), `referral_codes`, and `feedback`
(content-free, readable by operations). Goal achievements are self-reported, so points
can be gamed by ticking an inflated goal; acceptable for demo points, not for real money.
Voucher redemption is a simulation.
