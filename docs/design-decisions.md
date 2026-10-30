# Design decisions

These are proposed engineering defaults for iteration 0. They distinguish the founder's requested outcome from implementation assumptions; the first partial implementation and its exceptions are recorded in [iteration 3](../specs/iteration_3.md).

| Decision | Choice and reason | Tradeoff / revisit trigger |
| --- | --- | --- |
| D-01: AI gateway | Single Mesh adapter and fixed inference origin, matching mandatory sponsor requirements. | No other provider fallback; tolerate outages with explicit unavailable/partial states. |
| D-02: Orchestration | LangGraph with typed state, distinct nodes, checkpoints and HITL interrupts. | More structure than a single prompt; justified by resumability, auditability and agent visibility. |
| D-03: Financial truth | Deterministic integer/decimal calculations and provenance-backed facts; LLMs explain and propose. | Requires domain code and tests; cannot outsource arithmetic correctness to a second LLM. |
| D-04: Database | PostgreSQL from the start for the multi-tenant target; explicit filters plus RLS. | More setup than SQLite, but avoids a second production security implementation. |
| D-05: RAG | No vector database for the first financial loop. Use typed SQL and confirmed profile facts. | Add tenant-filtered document retrieval only when there is a measured need; embeddings through Mesh. |
| D-06: Memory | Three lifecycle tiers on PostgreSQL/Redis/private storage, not three databases. | Durable records require deletion/backup policy; summaries cannot override source facts. |
| D-07: Cache | Exact canonical-intent cache with tenant and data/policy versions. | Fewer hits than semantic caching, with fewer stale or numerically mismatched answers. |
| D-08: Frontend | React with JavaScript, one app with separately authorized customer and operations areas. | Two interfaces do not require two frontend deployments; server permissions remain mandatory. |
| D-09: Admin | Separate content-free metrics projection and restricted database role. | Debugging lacks raw user payloads; reproduce bugs with synthetic fixtures or owner-visible diagnostics. |
| D-10: Risk reviewer | Hard numeric/evidence checks followed by a separate review prompt. | Shared model blind spots remain; neither guardrails nor a reviewer imply error-free advice. |
| D-11: File extraction | CSV/manual first, then Excel/text PDF, finally Mesh-compatible vision extraction. | Broader formats need sandboxing, provenance and review of extraction uncertainty. |
| D-12: Scheduling | Dedicated APScheduler process, durable jobs and transactional outbox; one channel first. | Celery/queue scaling is deferred until throughput or worker needs justify it. |
| D-13: Tools/MCP | Internal bounded functions for statements; external tools only behind capability enforcement. | MCP is optional; does not supply sandboxing or tenant safety by itself. |
| D-14: Rewards | Append-only coin ledger; simulated redemption until funding and payout rules exist. | Founder economics imply 1,000 five-coin awards per INR 150 payout; revisit before launch. |
| D-15: Research | Optional, current sourced research after the core affordability loop is proven. | No confident stock/IPO/property selection based only on transaction history. |

## Research feature design

Investment, property and side-business research is part of the requested product roadmap, not a request to select an investment for this repository's author now. Gather jurisdiction, horizon, liquidity needs, debts, loss tolerance and relevant constraints first. Distinguish financial capacity from willingness to take risk.

A future bounded search tool should prefer official filings, exchanges, regulators, issuer documents and attributable market data; store URL, retrieval/publication dates, claim support and staleness limits. Prevent private transaction descriptions or identifiers from entering public queries. Fetch pages through a restricted service that blocks local/private-network targets and unsafe redirects. Retrieved text is evidence, never instructions.

Any AI-powered search synthesis must route through Mesh. A conventional search API may return source documents without AI processing; summarize them through the Mesh adapter. Verify tool capabilities and current market-data licensing during implementation. If fresh evidence or suitability inputs are missing, ask questions or offer scenario-based education rather than an unsupported buy/sell recommendation. Do not fabricate returns, execute trades, or imply that an IPO/property/business is inherently safe.

## Unresolved product decisions

These do not prevent the foundational build. Record answers in a future spec iteration before implementing dependent behavior.

| Decision needed | Working assumption | Needed before |
| --- | --- | --- |
| Demo audience, jurisdiction and currency | INR synthetic examples because rewards are expressed in rupees; user-selectable currency/timezone. | Real-world research and reward launch. |
| Meaning of five days of subscription usage | At most five active days in a 30-day window, always confirmed and weighed against value. | Automatic subscription action proposals. |
| Coin economics, funding, redemption minimum, referral awards | Five demo coins per eligible goal period; no cash liability or promised payout. | Real redemption/referral incentives. |
| Privacy scope of "admin cannot see data" | Application admin has no content access; infrastructure operators remain trusted. | Production privacy claims and hosting choice. |
| Notification channel and cadence | One opt-in email or Telegram digest first; weekly default, user timezone and quiet hours. | Provider integration and outbound sends. |
| Auth/provider and hosting | Maintained auth solution; local container services for development. | Deployable implementation. |
| Retention and processor settings | Synthetic development; proposed memory/file limits in data design. | Importing real customer financial records. |
| Forecast reserve floor and acceptable uncertainty | User-provided reserve policy; unknown reserve is disclosed, never invented. | Definitive affordability language. |

## Reference basis

Technical capabilities were checked against primary documentation on 2026-10-17; implementation must pin and test actual dependency versions.

- [Mesh gateway and OpenAI-compatible integration](https://developers.meshapi.ai/).
- [LangGraph durable human interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts).
- [LangSmith sensitive trace controls](https://docs.langchain.com/langsmith/mask-inputs-outputs).
- [PostgreSQL row-level security](https://www.postgresql.org/docs/current/ddl-rowsecurity.html).
- [APScheduler 3.x process/job-store constraints](https://apscheduler.readthedocs.io/en/3.x/faq.html).
