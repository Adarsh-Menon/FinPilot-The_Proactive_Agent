<p align="center">
  <img src="resources/finpilot-logo.png" alt="FinPilot Logo" width="650"/>
</p>

<p align="center">
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"/></a>
  <a href="https://nodejs.org/"><img src="https://img.shields.io/badge/node-22+-green.svg" alt="Node.js 22+"/></a>
  <a href="https://fastapi.tiangolo.com/"><img src="https://img.shields.io/badge/backend-FastAPI-009688.svg" alt="FastAPI"/></a>
  <a href="https://react.dev/"><img src="https://img.shields.io/badge/frontend-React%2018-61DAFB.svg" alt="React"/></a>
  <a href="tests/"><img src="https://img.shields.io/badge/tests-177%20passed-success.svg" alt="Tests"/></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"/></a>
</p>

<p align="center">
  <strong>An encrypted, privacy-first, proactive financial companion and hybrid cash-flow forecaster.</strong>
</p>

FinPilot combines deterministic financial accounting, hybrid time-series forecasting, and a grounded multi-agent pipeline with risk verification and operations observability.

> **Status:** Local development build. Outbound scheduled digests, isolated file-parser workers, and KMS-backed key rotation remain future iterations. See [build status](docs/build-status.md) and [evaluation protocol and results](evals/README.md) for measured results and limitations.

---

## Demo & Concept Video

<p align="center">
  <video src="resources/FinPilot_concept_60s_VO.mp4" controls="controls" width="100%" style="max-height: 500px;">
    Your browser does not support the video tag.
  </video>
</p>

> 🎬 **Watch the 60-second walkthrough:** [FinPilot Concept Video (MP4)](resources/FinPilot_concept_60s_VO.mp4)

---

---

## What We Built

**FinPilot** is an encrypted, privacy-first financial companion and cash-flow forecaster. Built with a **FastAPI** backend and a **React 18** frontend, it solves personal finance anxiety by coupling rigorous mathematical accounting with machine learning and collaborative multi-agent reasoning:

1. **Hybrid Cash-Flow Forecaster**: Combines Amazon Chronos-Bolt (`amazon/chronos-bolt-small` time-series foundation model) for everyday variable spending with exact deterministic scheduled flows, generating 1,000 correlated Monte Carlo paths ($\rho = 0.5$) to forecast cash balance percentiles (10th–90th) and reserve breach probabilities over 7- to 90-day horizons (with a trailing-mean fallback).
2. **Interactive What-If Purchase Overlays**: Allows users to test hypothetical expenditures against an immutable forecast baseline to verify affordability and reserve impact before money is spent.
3. **Application-Layer Envelope Encryption**: Secures all sensitive financial records using per-tenant AES-256-GCM keys and Argon2id password hashing, wrapped by a local master key (`~/.finpilot/master.key`).
4. **Operations & Observability Portal (`/admin`)**: Provides real-time token and dollar cost tracking, auto-pause spend caps, Telegram alerting for budget/abuse events, and automated backtest and DeepEval evaluation runners.

---

## How Our Agents Cooperate

FinPilot uses a LangGraph-orchestrated multi-agent pipeline where specialized agents cooperate with strict boundaries and deterministic supervisor verification:

```
[User Message / Voice Input]
             │
             ▼
   ┌───────────────────┐
   │ Coordinator Agent │ ──► Entity Extraction & Intent Classification
   └─────────┬─────────┘
             │
   ┌─────────┴─────────────────────────────────────────┐
   │                   Specialist Agents                │
   ▼                                                   ▼
┌───────────────────┐                       ┌──────────────────────┐
│  Categorizer &    │                       │   Forecaster Agent   │
│Investigator Agent │                       │  (Hybrid TSFM + MC)  │
└───────────────────┘                       └──────────┬───────────┘
                                                       │
                                            ┌──────────▼───────────┐
                                            │ Financial Advisor    │
                                            │       Agent          │
                                            └──────────┬───────────┘
                                                       │
                                            ┌──────────▼───────────┐
                                            │ Risk Reviewer Agent  │
                                            │ (Dedicated Qwen LLM) │
                                            └──────────┬───────────┘
                                                       │
                                            ┌──────────▼───────────┐
                                            │     Deterministic    │
                                            │   Supervisor Node    │
                                            └──────────┬───────────┘
                                                       │ (Validated)
                                                       ▼
                                            [Structured User Reply]
```

- **Coordinator Agent (`backend/app/mesh.py`)**: Classifies user intents (explaining cash flow, what-if purchases, transaction categorization, goals, debt payoff plans) and extracts structured entities (amounts, dates, categories).
- **Categorizer & Investigator Agent (`backend/app/investigator.py`)**: Labels transactions using few-shot merchant learning, learns user correction rules dynamically, and detects duplicate records during CSV imports.
- **Forecaster Agent (`backend/app/finance.py`, `backend/app/assistant.py`)**: Generates timeline rows and structures answers using strict `{fact_key}` place-holders (e.g., `{balance_at_horizon_end_likely_low}`) so that LLMs cannot hallucinate unverified financial balances.
- **Financial Advisor Agent (`backend/app/advisor.py`)**: Synthesizes actionable savings routes, emergency fund buffers, and debt payoff sequences strictly grounded in verified facts.
- **Risk Reviewer Agent (`backend/app/mesh.py`)**: Uses a dedicated risk model (`qwen/qwen3.5-122b-a10b` via Mesh) to audit advisor outputs against policy constraints (e.g., never recommend cancelling essential utilities or risk catastrophic balance dips).
- **Deterministic Supervisor Node (`backend/app/supervisor.py`)**: The final gatekeeper. It programmatically verifies that all `{fact_key}` replacements match exact ledger values, checks numerical bounds, and strips ungrounded claims. If a check fails, it safely falls back to deterministic rule-based output.

---

## Key Design Decisions

1. **Mesh API as Mandatory Unified AI Gateway**:
   - All LLM inference strictly routes through `https://api.meshapi.ai/v1` via the official Mesh endpoint using an `enforce_origin` HTTP hook.
   - Credentials can never leak to unauthorized origins or alternative endpoints.
2. **Deterministic & Foundation Model Grounding over Pure Generative LLMs**:
   - Generative LLMs are never allowed to compute financial balances, forecast quantiles, or declare purchase affordability on their own.
   - All cash balances are calculated programmatically or predicted via `amazon/chronos-bolt-small` with Monte Carlo simulations, while the LLM solely synthesizes explanations.
3. **Zero-Knowledge Envelope Encryption**:
   - Every tenant's records are encrypted at rest with unique per-customer AES-256-GCM data encryption keys (DEKs).
   - DEKs are encrypted using the application master key stored outside the repository (`~/.finpilot/master.key`). Database compromise alone yields zero decipherable financial records.
4. **Content-Free Observability & Privacy**:
   - Prometheus metrics (`/metrics`), Grafana boards, and LangSmith traces are strictly sanitized to metadata (tokens, latency, error status, prompt version checksums), ensuring no PII or raw financial transactions ever reach logs.

---

## Self-Evaluation & Benchmark Results

Our implementation is validated across unit tests, DeepEval LLM judge suites, and rolling-origin backtests:

| Evaluation Dimension | Benchmark / Protocol | Target / Gate | Measured Result | Status |
|---|---|---|---|---|
| **Unit & Integration Suite** | `pytest tests/backend -q` (180 tests) | 100% passing (excluding unconfigured PG) | **177 passed, 3 skipped** | **PASS** |
| **Categorization Accuracy** | 38-case curated golden holdout (`evals/goldens/categorizer.json`) | Macro-F1 $\ge 0.85$ | **Macro-F1: 0.91** | **PASS** |
| **Supervisor Grounding** | 34 end-to-end intent scenarios (`backend/app/supervisor.py`) | 0 ungrounded amounts / zero guarantees | **34/34 verified grounded (0 leaks)** | **PASS** |
| **Forecaster Explanation** | Fact placeholder verification & code gate checks | 100% facts grounded in calculation | **8/8 grounded, 0 false guarantees** | **PASS** |
| **Forecast Accuracy (7-day)** | Rolling backtest across 6 personas $\times$ 6 cutoffs (`20261028-calibrated`) | Outperform trailing-mean WAPE | **13.4% WAPE (Hybrid) vs 21.9% (Baseline)** | **PASS** |
| **Forecast Accuracy (30-day)** | Rolling backtest across 6 personas $\times$ 6 cutoffs (`20261028-calibrated`) | Balance trajectory MAE | **MAE ₹7,597 (Hybrid) vs ₹7,081 (Baseline)** | **PASS** |
| **Reserve Breach Detection** | Shortfall classification (`below_reserve_probability > 0.5`) | Precision & Recall $\ge 95\%$ | **100.0% Recall, 100.0% Precision** | **PASS** |
| **Risk Reviewer Gate** | Safety policy audit on unsafe actions / mutations | Unsafe mutation block rate $\ge 98\%$ | **9/9 unsafe mutations blocked (100%)** | **PASS** |
| **Advisor Grounding** | 100 synthetic persona cases (`evals/advisor_eval.py`) | $\ge 98\%$ grounded, 0 critical failures | **100% grounded, 0 unsupported claims** | **PASS** |

## Getting Started

### Prerequisites

- **Python**: 3.11 or higher
- **Node.js**: 22 or higher (with npm)
- **uv** (recommended for fast Python package management) or standard `pip`

### 1. Environment Setup

Copy the example environment file:

```bash
# Windows (PowerShell)
Copy-Item .env.example .env

# macOS / Linux
cp .env.example .env
```

> **Note:** FinPilot runs out-of-the-box with synthetic data and deterministic fallback logic without requiring external LLM API keys. To enable live AI reasoning, set `MESH_API_KEY` in `.env`.

### 2. Backend Installation & Startup

#### Using PowerShell (Windows):

```powershell
# Create and activate virtual environment
uv venv .venv --python 3.11
uv pip install --python .venv/Scripts/python.exe -r requirements.txt

# Optional: Install CPU Chronos-Bolt foundation model for hybrid forecasting
uv pip install --python .venv/Scripts/python.exe -r requirements-forecast.txt --extra-index-url https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match

# Start backend server
.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8010 --reload
```

#### Using Bash (macOS / Linux):

```bash
# Create and activate virtual environment
uv venv .venv --python 3.11
source .venv/bin/activate
uv pip install -r requirements.txt

# Optional: Chronos-Bolt forecast foundation model
uv pip install -r requirements-forecast.txt --extra-index-url https://download.pytorch.org/whl/cpu

# Start backend server
uvicorn backend.app.main:app --host 127.0.0.1 --port 8010 --reload
```

The API will be available at [http://127.0.0.1:8010](http://127.0.0.1:8010) (Interactive docs at [http://127.0.0.1:8010/docs](http://127.0.0.1:8010/docs)).

### 3. Frontend Installation & Startup

In a second terminal:

```bash
# Navigate to frontend and install dependencies
cd frontend
npm ci

# Start Vite development server
npm run dev
```

Open [http://localhost:5173](http://localhost:5173) in your browser.

---

## Exploring the App

1. **Synthetic Demo Account:**
   - Register a new account or sign in.
   - Click **Explore an example** to populate a pre-built 60-day transaction history with automated categorization and a calculated forecast.
2. **Operations & Admin Portal:**
   - Visit [http://localhost:5173/admin](http://localhost:5173/admin).
   - Create an admin operator via CLI:
     ```bash
     python -m backend.manage create-operator --username youradmin
     ```
3. **Optional Observability Stack:**
   - Start local Prometheus and Grafana instances via Docker:
     ```bash
     docker compose -f compose.observability.yaml up -d
     ```
   - Access Grafana at [http://localhost:3000](http://localhost:3000).

---

## Verification & Testing

Run the test suite to verify the installation:

```bash
# Backend unit & integration tests (177 tests)
pytest tests/backend -q

# Hybrid forecaster backtest benchmark
python -m backend.app.backtest --model amazon/chronos-bolt-small

# Prompt release version validation
node scripts/prompt-versions.mjs validate

# Frontend production build
cd frontend && npm run build
```

---

## Project Documentation

- **[Current Build Status](docs/build-status.md)**: Verified test coverage, known limitations, and next steps.
- **[Context Summary](context.md)**: Architectural iteration summary and forecast audit.
- **[Local Development Guide](docs/local-development.md)**: Configuration, PostgreSQL setup, Telegram alerts, and debugging.
- **[System Architecture](docs/architecture.md)**: Multi-agent topology, supervisor guardrails, and request lifecycles.
- **[Security & Operations](docs/security-and-operations.md)**: Encryption envelope, key management, and session safety.
- **[Evaluation Protocols](evals/README.md)**: DeepEval benchmarks, rolling-origin backtests, and judge prompts.
- **[Data & API Design](docs/data-and-api.md)**: Schemas, endpoints, and storage interfaces.
- **[Prompt Versioning](docs/prompt-versioning.md)**: Checksums and independent agent prompt management.
- **[Specification Archive](specs/README.md)**: Historical specs across iterations 0 through 19.

---

## Security

- Do not commit `.env` or any production secrets to source control.
- Ensure `~/.finpilot/master.key` is backed up safely; loss of this key prevents decryption of the application database.
