# TraceIQ — KPI Intelligence-to-Action Engine

> **"No evidence → No confident story."**
> The LLM only explains what deterministic logic has already proven.

TraceIQ is a KPI intelligence-to-action engine for Retail / E-commerce analytics. It automatically detects material KPI movements, traces *why* they happened across structured and unstructured data sources, and recommends a concrete next action — all with transparent confidence scoring and full evidence lineage. Every claim in the narrative is traceable back to raw data.

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [What TraceIQ Does](#2-what-traceiq-does)
3. [Architecture Overview](#3-architecture-overview)
4. [Pipeline Layers](#4-pipeline-layers)
5. [Technology Stack](#5-technology-stack)
6. [Project Structure](#6-project-structure)
7. [Prerequisites](#7-prerequisites)
8. [Setup & Installation](#8-setup--installation)
9. [Running the Application](#9-running-the-application)
10. [Running Tests](#10-running-tests)
11. [API Endpoints](#11-api-endpoints)
12. [Security & Role-Based Access](#12-security--role-based-access)
13. [Telemetry & Cost Tracking](#13-telemetry--cost-tracking)
14. [MVP Scenario](#14-mvp-scenario)
15. [LLM vs Non-LLM Processing](#15-llm-vs-non-llm-processing)
16. [Feedback Loop](#16-feedback-loop)
17. [Documentation Index](#17-documentation-index)
18. [Implementation Status](#18-implementation-status)

---

## 1. Problem Statement

Dashboards show that a KPI moved (e.g. *"Revenue down 8%"*) but rarely explain **why**, or **what to do** about it. Translating a metric movement into an explanation and an action currently requires a human analyst and takes days.

TraceIQ collapses that cycle from days to seconds by:
- Detecting **real** KPI movements (separating signal from noise)
- Explaining **why** the movement happened using both structured and unstructured data
- Assigning a **confidence score** to each explanation and abstaining when evidence is weak
- Recommending a **concrete next action**, tied to traceable evidence
- Adapting the narrative to **who is asking** (persona-based)
- **Learning** from human feedback over time

---

## 2. What TraceIQ Does

| Capability | How it works |
|---|---|
| **Anomaly Detection** | Z-score vs rolling baseline + persistence check + business materiality threshold |
| **KPI Decomposition** | Mathematical formula decomposition → dimensional contribution analysis (region / category / channel) |
| **Hypothesis Generation** | Deterministic business rules first; LLM-assisted candidates only as a secondary, non-authoritative source |
| **Evidence Retrieval** | Structured (SQL against reconciled tables) + Unstructured (semantic RAG over tickets/notes via ChromaDB) |
| **Confidence Scoring** | Rule-based multi-factor scoring producing Strong / Moderate / Weak / Insufficient — never an LLM-invented percentage |
| **Ambiguity Gate** | Explicitly abstains and states what evidence is missing rather than producing a falsely confident answer |
| **Recommendations** | Structured driver → lever → action → owner → monitoring KPI — never generic advice |
| **Persona Narratives** | LLM synthesizes a persona-adapted narrative *only* from the pre-validated evidence package |
| **Feedback Loop** | Human decisions (accept / reject / edit / request more evidence) logged to PostgreSQL for future tuning |
| **Telemetry** | Every LLM call logged with latency, token counts, and estimated cost — surfaced in UI and API |

---

## 3. Architecture Overview

```
STRUCTURED DATA                   UNSTRUCTURED DATA
(orders, inventory,               (tickets, notes, reports
 delivery — daily/hourly)          — event-based, irregular)
        │                                  │
        └──────────── DATA LAYER ──────────┘
                       │  (reconciled to common daily KPI grain)
              KPI SEMANTIC LAYER
                       │  (definitions, formulas, dimensions, thresholds)
              CHANGE DETECTION
                       │  (baseline + z-score + persistence + materiality)
              KPI DECOMPOSITION
                       │  (which dimension drove the movement?)
              HYPOTHESIS ENGINE
                       │  (rule-based rules; LLM suggestion behind flag)
              EVIDENCE ENGINE
                       │  (SQL structured evidence + RAG unstructured evidence)
              CONFIDENCE ENGINE
                       │  (Strong / Moderate / Weak / Insufficient)
              AMBIGUITY GATE
                       │  (High / Moderate / Insufficient → abstain or proceed)
              RECOMMENDATION ENGINE
                       │  (driver → lever → action → owner → monitoring KPI)
              LLM LAYER
                       │  (persona-specific narrative synthesis ONLY)
              INVESTIGATION UI  (Streamlit)
                       │
              HUMAN DECISION  (accept / reject / edit / request more evidence)
                       │
              FEEDBACK LOOP  (logged to PostgreSQL for future tuning)
```

**Core principle:** The LLM sits at the *end* of the pipeline, not the start. Every upstream stage is deterministic and testable. The LLM only synthesizes and explains evidence that has already been produced and validated.

---

## 4. Pipeline Layers

| Layer | Module | Purpose |
|---|---|---|
| Data Layer | `data/generate_synthetic_data.py`, `db/migrations/` | Ingests + reconciles structured/unstructured sources to a common daily KPI grain |
| KPI Semantic Layer | `kpi_contract.yaml`, `src/semantic/kpi_contract.py` | Central registry of KPI definitions, formulas, dimensions, thresholds, access rules |
| Change Detection | `src/detection/change_detection.py` | Baseline + z-score + persistence + business materiality → NORMAL / WATCH / INVESTIGATE |
| KPI Decomposition | `src/decomposition/kpi_decomposition.py` | Formula decomposition + dimensional contribution analysis |
| Hypothesis Engine | `src/hypothesis/rules.py`, `src/hypothesis/llm_suggest.py` | Business-rule hypotheses; `llm_suggest.py` behind `LLM_SUGGEST_ENABLED` config flag |
| Evidence Engine | `src/evidence/structured_evidence.py`, `src/evidence/retrieval.py` | SQL evidence + ChromaDB semantic retrieval |
| Confidence Engine | `src/confidence/scoring.py` | Multi-factor weighted rule scoring → confidence label |
| Ambiguity Gate | `src/ambiguity/gate.py` | Routes to High / Moderate / Insufficient; explicit abstention output |
| Recommendation Engine | `src/recommendation/recommendation_engine.py` | Structured action card per confidence tier |
| LLM Layer | `src/llm/llm_client.py`, `src/llm/prompts.py`, `src/llm/narrative.py` | Provider-agnostic narrative synthesis (Claude or Gemini) |
| Pipeline Orchestrator | `src/pipeline/orchestrator.py` | Wires all stages end-to-end |
| API Layer | `api/` (FastAPI) | JWT auth, RLS session wiring, all REST endpoints |
| UI Layer | `ui/app.py` + `src/ui/app.py` (Streamlit) | 7-section Investigation Canvas |
| Feedback | `src/feedback/feedback_store.py` | Stores human decisions + summary view |
| Telemetry | `src/telemetry/logger.py` | Logs every LLM call with latency, tokens, cost |

---

## 5. Technology Stack

| Layer | Technology | Why |
|---|---|---|
| Data processing | Python 3.11 + Pandas 2.2.3 + NumPy 1.26.4 | Standard, fast iteration |
| Statistics / anomaly detection | statsmodels 0.14.4 + scikit-learn 1.5.2 | Rolling stats, z-scores, seasonal decomposition |
| Database | PostgreSQL 15 (Docker) / PostgreSQL 18 (native) | Real SQL semantics, row-level security (RLS) support |
| ORM / DB driver | SQLAlchemy 2.0.36 + psycopg2-binary 2.9.9 | Async-compatible, typed queries |
| Unstructured retrieval | sentence-transformers 3.3.1 + ChromaDB 0.5.20 | Local, free, embedding + vector search (all-MiniLM-L6-v2) |
| Backend / API | FastAPI 0.115.6 + Uvicorn 0.32.1 | Async, typed, OpenAPI auto-docs |
| LLM (narrative only) | anthropic 0.40.0 (Claude Haiku) or google-generativeai 0.8.3 (Gemini) | Swappable via `LLM_PROVIDER` env var — architecture unchanged |
| Frontend | Streamlit 1.62.0 + Plotly 7.0.0 | Fastest path to Investigation Canvas UI |
| Auth | python-jose 3.3.0 (JWT) + passlib 1.7.4 (bcrypt) | Realistic auth flow without OAuth complexity |
| Containerisation | Docker Compose (PostgreSQL 15-alpine) | Zero-config local Postgres |
| Config | python-dotenv 1.0.1 + pyyaml 6.0.2 | Environment and YAML contract loading |
| Testing | pytest 8.3.4 + pytest-asyncio 0.24.0 + httpx 0.28.1 | Unit + integration + E2E scenario tests |

> **Infrastructure cost for the prototype: ~$0** — all components are free/self-hosted; the only paid resource is the LLM API, isolated to one layer and bounded by caching + rate limiting.

---

## 6. Project Structure

```
TraceIQ/
├── api/                              # FastAPI backend
│   ├── __init__.py
│   ├── auth.py                       # JWT login + token validation (sha256_crypt)
│   ├── deps.py                       # FastAPI dependencies (auth, DB session, RLS wiring)
│   ├── main.py                       # App entry point (router registration)
│   └── routes/
│       ├── __init__.py
│       ├── alerts.py                 # /kpi/alerts endpoints (list + detail)
│       ├── feedback.py               # /feedback endpoint
│       └── telemetry.py              # /telemetry/summary endpoint
│
├── data/
│   ├── __init__.py
│   ├── generate_synthetic_data.py    # Generates all synthetic tables with injected MVP scenario
│   ├── chroma_db/                    # ChromaDB vector store (auto-created at runtime)
│   └── seed/                         # Pre-generated CSV seed files
│       ├── orders.csv                # 383,614 order rows (~54 MB)
│       ├── inventory_snapshots.csv   # 4,339 snapshot rows
│       ├── deliveries.csv            # 60,033 delivery rows (~7 MB)
│       └── tickets_notes.csv         # 83 support ticket / note rows
│
├── db/
│   ├── __init__.py
│   ├── connection.py                 # DB connection pool + session helpers
│   └── migrations/                   # SQL migration files (run in order)
│       ├── 000_create_schemas.sql    # Creates raw, kpi, pipeline schemas
│       ├── 001_raw_tables.sql        # Raw source tables (orders, inventory, deliveries, tickets)
│       ├── 002_kpi_views.sql         # Materialized KPI views (daily grain)
│       ├── 003_pipeline_tables.sql   # Pipeline output tables (alerts, feedback, telemetry)
│       └── 004_rls_policies.sql      # PostgreSQL Row-Level Security policies
│
├── src/                              # All pipeline modules
│   ├── __init__.py
│   ├── ambiguity/
│   │   └── gate.py                   # Ambiguity gate — routes to High / Moderate / Insufficient
│   ├── api/
│   │   └── main.py                   # Internal API module (pipeline invocation endpoint)
│   ├── confidence/
│   │   └── scoring.py                # Multi-factor weighted rule scoring → confidence label
│   ├── decomposition/
│   │   └── kpi_decomposition.py      # Formula decomposition + dimensional contribution analysis
│   ├── detection/
│   │   └── change_detection.py       # Baseline + z-score + persistence + materiality detection
│   ├── evidence/
│   │   ├── structured_evidence.py    # SQL queries → structured evidence items
│   │   └── retrieval.py              # ChromaDB RAG retrieval over unstructured tickets/notes
│   ├── feedback/
│   │   └── feedback_store.py         # PostgreSQL feedback_log read/write + summary
│   ├── hypothesis/
│   │   ├── rules.py                  # Deterministic business-rule hypothesis generation
│   │   └── llm_suggest.py            # Optional LLM hypothesis suggestions (behind flag)
│   ├── llm/
│   │   ├── llm_client.py             # Provider-agnostic client (Claude / Gemini) with caching
│   │   ├── prompts.py                # Prompt templates (analyst + business_leader personas)
│   │   └── narrative.py              # Narrative assembly — injects pre-validated evidence
│   ├── pipeline/
│   │   └── orchestrator.py           # End-to-end stage orchestrator
│   ├── recommendation/
│   │   └── recommendation_engine.py  # Structured action cards per confidence tier
│   ├── semantic/
│   │   └── kpi_contract.py           # Loads and validates kpi_contract.yaml
│   ├── telemetry/
│   │   └── logger.py                 # Logs every LLM call (latency, tokens, cost) to PostgreSQL
│   └── ui/
│       └── app.py                    # Streamlit UI helper components
│
├── tests/                            # Test suite — one file per pipeline stage
│   ├── __init__.py
│   ├── test_stage0_setup.py          # DB connectivity + schema creation
│   ├── test_stage1_data_and_contract.py   # Synthetic data + KPI semantic contract
│   ├── test_stage2_change_detection.py    # Noise vs true-anomaly detection
│   ├── test_stage3_decomposition.py       # KPI decomposition + contribution analysis
│   ├── test_stage4_hypothesis.py          # Rule-based hypothesis generation
│   ├── test_stage5_structured_evidence.py # SQL structured evidence retrieval
│   ├── test_stage6_rag.py                 # ChromaDB RAG unstructured evidence retrieval
│   ├── test_stage7_confidence.py          # Confidence engine scoring
│   ├── test_stage8_ambiguity_gate.py      # Ambiguity gate + abstention logic
│   ├── test_stage9_recommendation.py      # Recommendation engine (Strong/Moderate/Insufficient)
│   ├── test_stage10_llm.py                # LLM layer — hallucination + persona narrative tests
│   ├── test_stage11_api.py                # FastAPI endpoints + role-based security
│   ├── test_stage13_14_feedback_telemetry.py  # Feedback loop + telemetry logging
│   └── test_stage15_e2e.py                # Full end-to-end scenario suite (11 scenarios)
│
├── ui/
│   └── app.py                        # Streamlit Investigation Canvas entry point (7 sections)
│
├── kpi_contract.yaml                 # KPI definitions, formulas, thresholds, lineage rules
├── docker-compose.yml                # PostgreSQL 15-alpine local container
├── requirements.txt                  # Pinned Python dependencies (Python 3.11+)
├── .env.example                      # Environment variable template (copy to .env)
└── .gitignore
```

---

## 7. Prerequisites

- **Python 3.11+**
- **Docker Desktop** (for PostgreSQL via Docker Compose), **or** a local PostgreSQL 15+ installation
- **An LLM API key**: either Anthropic (Claude) or Google Gemini — set via `.env`

---

## 8. Setup & Installation

### Step 1 — Clone the repository

```bash
git clone https://github.com/<your-username>/TraceIQ.git
cd TraceIQ
```

### Step 2 — Create and activate a virtual environment

```bash
# Windows (PowerShell)
python -m venv .venv
.venv\Scripts\Activate.ps1

# macOS / Linux
python -m venv .venv
source .venv/bin/activate
```

### Step 3 — Install dependencies

```bash
pip install -r requirements.txt
```

### Step 4 — Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` and fill in your values:

```env
DATABASE_URL=postgresql://traceiq_user:changeme@localhost:5432/traceiq

# Choose one: "claude" or "gemini"
LLM_PROVIDER=claude
ANTHROPIC_API_KEY=your_anthropic_api_key_here
GEMINI_API_KEY=your_gemini_api_key_here

# JWT settings
JWT_SECRET=change_this_to_a_long_random_secret_before_use
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=480
```

### Step 5 — Start PostgreSQL

**Using Docker (recommended):**

```bash
docker compose up -d
```

**Or** point `DATABASE_URL` to your existing local PostgreSQL 15+ (or 18+) service.

### Step 6 — Run database migrations

```bash
python -m db.migrations
```

This creates schemas `raw`, `kpi`, and `pipeline`, then applies all raw tables, KPI views, pipeline tables, and Row-Level Security policies in order.

### Step 7 — Generate synthetic data

```bash
python data/generate_synthetic_data.py
```

This generates and loads:
- **383,614** order rows (~54 MB CSV)
- **4,339** inventory snapshot rows
- **60,033** delivery rows (~7 MB CSV)
- **83** support ticket / note rows (including the planted outage and competitor signals)

Pre-generated CSVs are also available under `data/seed/` for offline use.

---

## 9. Running the Application

### FastAPI backend

```bash
uvicorn api.main:app --reload --port 8000
```

Interactive API docs: [http://localhost:8000/docs](http://localhost:8000/docs)

### Streamlit Investigation Canvas

```bash
streamlit run ui/app.py
```

UI: [http://localhost:8501](http://localhost:8501)

### Demo Users (seeded)

| Username | Password | Role | Region Access |
|---|---|---|---|
| `north_leader` | `demo1234` | `regional_leader` | North only |
| `analyst` | `demo1234` | `analyst` | All regions |

---

## 10. Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run a specific stage
pytest tests/test_stage2_change_detection.py -v

# Run the full end-to-end scenario suite
pytest tests/test_stage15_e2e.py -v
```

### Test Results (as of 2026-08-30)

| Stage | File | Tests | Status |
|---|---|---|---|
| Stage 0 — Setup | `test_stage0_setup.py` | 3/3 | ✅ |
| Stage 1 — Data & Contract | `test_stage1_data_and_contract.py` | 25/25 | ✅ |
| Stage 2 — Change Detection | `test_stage2_change_detection.py` | 15/15 | ✅ |
| Stage 3 — KPI Decomposition | `test_stage3_decomposition.py` | 19/19 | ✅ |
| Stage 4 — Hypothesis Generation | `test_stage4_hypothesis.py` | 14/14 | ✅ |
| Stage 5 — Structured Evidence | `test_stage5_structured_evidence.py` | 25/25 | ✅ |
| Stage 6 — RAG Evidence | `test_stage6_rag.py` | 16/16 | ✅ |
| Stage 7 — Confidence Engine | `test_stage7_confidence.py` | 15/15 | ✅ |
| Stage 8 — Ambiguity Gate | `test_stage8_ambiguity_gate.py` | 20/20 | ✅ |
| Stage 9 — Recommendation Engine | `test_stage9_recommendation.py` | 17/17 | ✅ |
| Stage 10 — LLM Layer | `test_stage10_llm.py` | 19/19 | ✅ |
| Stage 11 — API Layer | `test_stage11_api.py` | 19/19 | ✅ |
| Stage 13/14 — Feedback & Telemetry | `test_stage13_14_feedback_telemetry.py` | 15/15 | ✅ |
| Stage 15 — End-to-End | `test_stage15_e2e.py` | 33/33 | ✅ |
| **Total** | | **255/255** | **✅** |

> **Note:** End-to-end suite runtime ~7m 12s. Covers all 11 scenarios from `docs/15_TESTING_STRATEGY.md`: Noise, True Anomaly, Root-Driver, Contradiction, Ambiguity, Hallucination Guard, Recommendation Relevance, Sparse History, Role-Based Security, Persona Narrative, and Feedback Loop.

---

## 11. API Endpoints

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `/auth/login` | POST | No | Issue JWT token (username + password) |
| `/kpi/alerts` | GET | Yes | List KPI alerts (RLS-filtered by role/region) |
| `/kpi/alerts/{alert_id}` | GET | Yes | Full alert: decomposition, hypotheses, evidence |
| `/kpi/alerts/{alert_id}/narrative` | GET | Yes | Persona-adapted LLM narrative (`analyst` or `business_leader`) |
| `/kpi/alerts/{alert_id}/recommendation` | GET | Yes | Structured recommendation card |
| `/feedback` | POST | Yes | Submit human decision (accept / reject / edit / request_more_evidence) |
| `/telemetry/summary` | GET | Analyst only | Latency / token / cost summary |
| `/pipeline/run` | POST | Analyst only | Manually trigger a detection run |

---

## 12. Security & Role-Based Access

Security is enforced at **two independent layers** (defence in depth):

1. **Application layer** — FastAPI checks role claims in the JWT before executing endpoint logic.
2. **Database layer** — PostgreSQL RLS policies filter rows based on `app.current_user_region`, set from the JWT on every request. Even a buggy API endpoint cannot leak cross-region data.

| Role | Capabilities |
|---|---|
| `regional_leader` | View own region's alerts + narratives; submit feedback |
| `analyst` | View all regions; trigger pipeline runs; view telemetry |

No raw customer PII is included in the dataset — `customer_segment` is a category, not an identifier.

---

## 13. Telemetry & Cost Tracking

Every LLM call is logged to `telemetry_log` with:

| Field | Description |
|---|---|
| `latency_ms` | Wall-clock time for the LLM call |
| `input_tokens` / `output_tokens` | From provider API usage metadata |
| `estimated_cost_usd` | Token counts × provider per-token rate |
| `llm_provider` | `claude` or `gemini` |
| `alert_id` | Links back to the specific investigation |
| `cached` | Whether the response was served from in-memory cache |

Telemetry is surfaced in the **Streamlit UI footer** (analyst view only) and the `/telemetry/summary` endpoint.

---

## 14. MVP Scenario

**Domain:** Retail / E-commerce
**KPIs tracked:** Revenue, Orders, AOV, Inventory Availability, Delivery SLA

**Injected scenario:** Revenue ↓ 8.2%, driven by North-region Electronics order decline, caused by a warehouse inventory outage — with a secondary weak signal (competitor activity) correctly marked "Insufficient Evidence."

### Evidence Data Contract

Every evidence item carries these mandatory fields:

| Field | Description |
|---|---|
| `source_freshness` | Timestamp of the underlying data as of the analysis run |
| `analytical_method` | Technique used (e.g. "z-score vs 90-day rolling baseline") |
| `contribution` | Quantitative share of the KPI movement this factor explains |
| `confidence_label` | Strong / Moderate / Weak / Insufficient |
| `lineage` | Source table + query reference — every claim traceable to raw data |

### Confidence Labels

| Label | Meaning |
|---|---|
| **Strong** | Multiple independent evidence sources align; high contribution; no contradicting signal |
| **Moderate** | Evidence supports the hypothesis but with gaps (e.g. sparse history caps Strong → Moderate) |
| **Weak** | Some evidence exists but contradicting signals reduce confidence |
| **Insufficient** | Evidence missing or contradictory — system abstains and states what is needed |

---

## 15. LLM vs Non-LLM Processing

| Step | LLM? | Method |
|---|---|---|
| Anomaly detection | ❌ | Rolling stats, z-score, seasonal decomposition |
| KPI decomposition | ❌ | Arithmetic formula breakdown + groupby contribution |
| Hypothesis rules | ❌ | Deterministic if/else business logic (`rules.py`) |
| Structured evidence | ❌ | SQL queries against PostgreSQL |
| Unstructured evidence | ❌ | Sentence-Transformers + ChromaDB vector search |
| Confidence scoring | ❌ | Weighted rule-based scoring |
| Ambiguity gating | ❌ | Threshold logic on confidence labels |
| Recommendation | ❌ | Structured template driven by confidence tier |
| **Narrative generation** | ✅ | LLM synthesizes from pre-validated evidence package |
| Hypothesis suggestion (optional) | ✅ | `llm_suggest.py` behind `LLM_SUGGEST_ENABLED` flag, non-authoritative |

The LLM is **explicitly forbidden** from computing anomalies, doing KPI math, inventing confidence scores, declaring causality, or generating evidence. A `GUARDRAIL` marker is injected into prompts for any zero-evidence hypothesis to prevent hallucinated justifications.

---

## 16. Feedback Loop

| Decision | Meaning |
|---|---|
| `accept` | Analyst/leader agrees with the recommendation and will act |
| `reject` | Recommendation is wrong or not actionable |
| `edit` | Recommendation is partially correct — analyst edits before acting |
| `request_more_evidence` | Confidence is insufficient; more data needed before acting |

Feedback is stored in `feedback_log` with `user_id`, `role`, `timestamp`, and decision — forming the foundation for future threshold and weight tuning. The `get_summary()` function returns decision counts and `accept_rate` for monitoring model quality over time.

## Latency Budget (Design Targets)

| Stage | Target |
|---|---|
| Change detection + decomposition | < 1s |
| Evidence retrieval (structured + RAG) | < 2s |
| Confidence scoring + ambiguity gate | < 0.5s |
| LLM narrative generation (per persona) | 2–5s |
| **Total per alert (both personas)** | **~5–10s** |

---

## Known Dependency Note

`starlette` is pinned to `>=0.37.2,<0.39.0` in `requirements.txt`. Streamlit 1.62 ships with starlette 1.6, which breaks the FastAPI `TestClient`. This pin keeps both frameworks functional in the same environment.

---

## License

This project is submitted as a prototype / research demonstrator. See repository for licensing details.
