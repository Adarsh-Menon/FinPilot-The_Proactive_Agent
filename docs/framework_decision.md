# Framework and model decisions

Date: 2026-10-28. Status: a record of choices made so far, with the alternatives and when to revisit each.
Prices are the gateway's list prices on 2026-10-27, in US dollars per million tokens (input / output).

Each section says what was chosen, why, what else was considered, and what evidence would change it.
Where a choice was made by preference rather than measurement, it says so.

## 1. Agent framework: LangGraph (not LangChain, Agno or others)

**What FinPilot actually uses.** LangGraph's `StateGraph` runs the forecast pipeline (ingestion →
behavioural analysis → forecaster → numeric checks), and LangSmith traces it. **LangChain itself is not
used**: only `langchain-core` is installed, as a dependency of LangGraph and LangSmith. The agents are
not framework "agents": each is one structured call to a model with a fixed JSON schema, wrapped by
FinPilot's own validation and risk review, and the coordinator's routing is ordinary code.

**Why this shape.** FinPilot's hardest requirement is that no number shown to an owner comes from a
language model. That needs full control over every prompt, every output schema and every check between the model
and the screen. A framework that runs its own agent loop (deciding which tool to call, retrying, keeping
memory) hides exactly the steps that must be verified. LangGraph was chosen for the pipeline because it
is an explicit graph of plain functions: nothing runs that is not written down, each node can be timed
and traced, and it adds no prompts of its own.

| Option | What it is good at | Why not (for now) |
|---|---|---|
| **LangGraph** (chosen, for the pipeline) | Explicit, inspectable graphs of steps; tracing with LangSmith; can add tool-calling loops later | Its full value (cycles, human-in-the-loop, checkpoints) is not used yet |
| LangChain | Large library of model wrappers, chains, retrievers and integrations | Adds abstraction over prompts and outputs that FinPilot would have to work around to keep its checks; nothing it offers is needed today |
| Agno | Fast, lightweight agents with tools, memory, knowledge and multi-agent teams | Optimised for autonomous agents; FinPilot's agents are deliberately not autonomous. A strong candidate if Fina gets read-only tools |
| CrewAI | Role-based multi-agent collaboration | Agents talking to each other makes grounding harder to prove; not a fit for regulated money advice |
| LlamaIndex | Retrieval and document pipelines | FinPilot has no retrieval today |
| OpenAI Agents SDK | Tool-calling agents with handoffs and guardrails | Built around one provider's conventions; FinPilot must stay provider-neutral |
| Plain code only | Least to learn, nothing hidden | Loses the graph structure and tracing that LangGraph gives for free |

**Revisit when** Fina gets read-only tools (for example "how much did I spend on Swiggy in September?").
Then compare a LangGraph tool loop with Agno on the same golden scenarios, with the risk reviewer still
checking every answer.

## 2. Models: DeepSeek V4 Flash for the agents, Qwen 3.5 122B for risk review, Chronos-Bolt for everyday spending

**How the language models were chosen.** The owner chose them for low cost and strong structured output, before an
evaluation suite existed. They were **not** selected by a head-to-head comparison. The goldens in
`evals/goldens/` now make that comparison possible.

**DeepSeek V4 Flash** ($0.050 / $0.450, 1M-token context, structured output) handles routing,
categorization, explanations, advice wording and coaching. It is among the cheapest capable models, but
not uniquely so:

| Model | Input | Output | Notes |
|---|---|---|---|
| openai/gpt-oss-20b | 0.018 | 0.090 | Cheapest with structured output; smaller, weaker on nuanced wording |
| mistralai/mistral-small-24b | 0.050 | 0.080 | Very cheap output; 32K context |
| openai/gpt-5-nano | 0.050 | 0.400 | Same price band as DeepSeek V4 Flash |
| **deepseek/deepseek-v4-flash** (chosen) | **0.050** | **0.450** | 1M context; good JSON adherence in this project's tests |
| google/gemini-2.5-flash-lite | 0.100 | 0.400 | Also accepts audio (useful for voice) |
| anthropic/claude-haiku-5.5 | 0.100 | 0.500 | Strong instruction following; double the input price |
| openai/gpt-5-mini | 0.250 | 2.000 | Higher quality tier, about 4–5× the price |

**Qwen 3.5 122B** ($0.260 / $2.080) reviews every piece of advice and explanation before it is shown.
The main reason for a **different model family** is independence: a reviewer from another lab is less
likely to share the writer's blind spots, so the two make fewer correlated mistakes. Alternatives worth
testing:

| Model | Input | Output | Why consider it |
|---|---|---|---|
| openai/gpt-oss-safeguard-20b | 0.075 | 0.300 | Built for policy and safety classification; about 3.5× cheaper input |
| anthropic/claude-haiku-5.5 | 0.100 | 0.500 | Different family from both current models; strong at following rubrics |
| google/gemini-2.5-flash-lite | 0.100 | 0.400 | Cheap, different family |
| **qwen/qwen3.5-122b-a10b** (chosen) | **0.260** | **2.080** | Independent family; large model for careful reading |

**How to decide on evidence.** Run `evals/deepeval` with each candidate as the agent model, and the risk
suite with each reviewer candidate. Choose the cheapest model that meets every gate in
`docs/evals-design.md`. For the reviewer, recall on unsafe answers matters far more than price, because
one missed unsafe answer costs more than any saving.

**Amazon Chronos-Bolt (`amazon/chronos-bolt-small`) for the hybrid forecaster (`backend/app/hybrid.py`).**
Known scheduled income and bills stay exact in code; only day-to-day non-recurring spending is forecast
as nine daily quantiles (`10%..90%`) by a frozen, pretrained time-series foundation model on CPU, then
combined with exact scheduled flows across 1,000 correlated Monte Carlo paths (`RHO = 0.5`). If
`chronos-forecasting` is not installed or `FORECAST_MODEL` is empty, the forecaster falls back to the
trailing-mean baseline. Both methods are compared on rolling-origin synthetic backtests
(`backend/app/backtest.py`, `evals/results/forecast/20261028-calibrated.json`) and content-free realized
accuracy (`ForecastAccuracy`).

## 3. Why these agents (and when there should be more or fewer)

The rule used: **a language model only where language is the job**. Anything that is arithmetic or a rule is code
(with the frozen time-series foundation model used strictly for daily spending quantiles inside the hybrid forecaster).

| Agent | Model? | Why it exists |
|---|---|---|
| Coordinator | Yes, plus code | Routes free text (typed or spoken) to one agent, then **verifies that agent's reply** before it is shown (`backend/app/supervisor.py`): no named securities, guarantees, verdicts or personal data; confirm-card amounts must be ones the owner said; amounts in explanations and goal notes must come from the agent's facts. A failed reply is replaced by a calculated answer or a safe message. It is a router with a final check, not a looping LLM supervisor |
| Categorizer | Yes | Merchant strings are messy and open-ended; owner labels and merchant rules are applied first, by code |
| Investigator | Yes | Phrases questions about missing income and bills from patterns that code detects |
| Forecaster (pipeline + Fina's answers) | TSFM for daily spending quantiles + LLM for prose explanation | `backend/app/hybrid.py` + `backend/app/finance.py` compute exact scheduled flows, daily spending quantiles (`amazon/chronos-bolt-small`) and 1,000 simulated paths; `backend/app/assistant.py` explains the resulting facts in plain words with `{fact_key}` placeholders |
| Advisor | Yes | Chooses and words options that code has calculated, with a "Why" |
| Goals coach | Yes | Writes a short encouraging note from calculated progress |
| Behavioural pattern agent | Yes | Describes spending trends that code has measured |
| Risk reviewer | Yes, a different model | Independent safety gate before anything is shown |
| Voice transcriber | Optional | Speech to text when the browser's recognition is not used |
| Debt, investment readiness, plans, numeric checks | **No** | Pure calculation: amortization, readiness rules, goal dates; a model would only add error |

**Could be fewer.** The goals coach, behavioural pattern agent and forecaster explainer all write short grounded
explanations with placeholders; they could become one "explainer" with a mode, saving prompts to maintain.
The cost is that each prompt would get broader and harder to evaluate. Merge them if the per-agent evals
show the same quality from one prompt.

**Could be more.** A tool-using Fina (read-only spending queries and what-ifs) would be one new agent.
A separate "education" agent would be needed if a curated knowledge source is added later.

**Cost per agent matters little at this scale.** Each owner action makes one to three model calls, mostly
under a few thousand tokens. The daily AI budget in the admin portal caps spending regardless.

## 4. Evaluation: DeepEval

| Option | Strengths | Why not chosen |
|---|---|---|
| **DeepEval** (chosen) | pytest-native, so evals sit next to the existing test suite; custom metrics, so code checks and judge scores share one report; GEval for rubric scoring; runs locally | Its default telemetry must be switched off (done); it pins an older `click`, so it runs in its own `.venv-evals` |
| RAGAS | Retrieval-quality metrics | FinPilot has no retrieval |
| promptfoo | Fast prompt comparisons from YAML; good for A/B of prompts | Node and YAML-based, separate from the Python code that must run around each agent |
| LangSmith evaluations | Built into the tracing already in use | Evaluation data would leave the environment; ties evaluation to one vendor |
| OpenAI Evals, Inspect AI, TruLens | Mature research harnesses | More setup; less natural for testing through FastAPI with real validation |

DeepEval is used in a deliberately narrow way. Code checks are the gate for anything exact. The judge
(a model different from the one under test) only scores tone, faithfulness and relevance. No evaluation
data goes to DeepEval's hosted platform.

## 5. Other platform choices

| Area | Chosen | Main alternatives | Why |
|---|---|---|---|
| API | FastAPI | Django, Flask | Typed request validation with Pydantic, async-ready, automatic API docs |
| Validation | Pydantic v2 | marshmallow, attrs | Same models validate API input and model output (strict JSON schemas) |
| Database | PostgreSQL with row-level security; SQLite for local development | Database per customer; schema per customer | One database to run, with isolation enforced by the database itself |
| Encryption | Per-customer data keys wrapped by a master key | Database-level encryption only | Database access alone, including by admins, cannot read customer data |
| Model client | OpenAI SDK against an OpenAI-compatible gateway (Mesh / OpenRouter) | LiteLLM; provider SDKs | One small, well-maintained client; switching provider is a URL and model name |
| Time-series forecaster | Amazon Chronos-Bolt (`chronos-forecasting` on CPU) + Monte Carlo simulation, with trailing-mean fallback | ARIMA/ETS, Prophet, LLM forecasting | Zero-shot quantiles on short daily series without per-user training; exact scheduled flows stay separate in code |
| Frontend | React 19 with Vite | Next.js | A single-page app with no need for server rendering; fast local builds |
| Monitoring | Prometheus and Grafana; LangSmith for model traces | Datadog, New Relic | Free, self-hosted, no customer data leaves; standard tools |
| Phone alerts | Telegram bot | SMS (Twilio), email, ntfy | Free, instant push, simple API; the owner's choice |
| Speech | Browser speech recognition; optional server speech model | Dedicated speech-to-text service | No cost by default; a server model can be switched on in configuration |

## 6. When to revisit

- **Models:** whenever the evaluation suite shows a cheaper model meeting every gate, or a model is retired
  or changes price by more than 2×.
- **Forecaster:** when realized accuracy (`ForecastAccuracy`) or larger rolling backtests (60/90-day horizons,
  more personas) show another Chronos-Bolt size or correlation parameter `RHO` improves coverage and pinball loss.
- **Framework:** when Fina gains tools or agents need multi-step loops. Prototype in LangGraph and Agno
  against the same system goldens.
- **Agents:** when two agents' evals show the same quality from one prompt (merge), or a new capability
  needs a new kind of output (split).
- **DeepEval:** if a judge metric disagrees with human labels by more than its calibrated tolerance, or the
  project needs retrieval metrics.
