# TraceIQ — Implementation Roadmap

## 1. Purpose
The exact stage-by-stage build order, matching `13_BACKEND_MODULE_PLAN.md` module structure.
Each stage must pass its tests (`15_TESTING_STRATEGY.md`) before the next stage begins. This
is the checklist that `00_IMPLEMENTATION_PROGRESS.md` tracks execution against.

## 2. Build Order

### Stage 0 — Project Setup
- Repo structure (`13_BACKEND_MODULE_PLAN.md` §2)
- Postgres running locally (Docker)
- `requirements.txt`, `.env.example`
- **Test**: DB connects, empty schema created successfully

### Stage 1 — Synthetic Dataset + KPI Semantic Contract
- `data/generate_synthetic_data.py` — generates `orders`, `inventory_snapshots`, `deliveries`,
  `tickets_notes` per `05_DATA_SCHEMA.md`, with the injected MVP scenario (`01_PRD.md` §6),
  the sparse-history SKU (§6.2), and the deliberately ambiguous competitor signal
- `kpi_contract.yaml` + `semantic/kpi_contract.py` per `04_KPI_SEMANTIC_CONTRACT.md` §8
- DB migrations + KPI views (`10_DATABASE_DESIGN.md` §7-8)
- **Test**: data loads correctly; KPI views return expected aggregates for a known date

### Stage 2 — Change Detection
- `detection/change_detection.py` — baseline, z-score, persistence, materiality
- **Test**: §4.1 (Noise) and §4.2 (True Anomaly) scenario tests pass

### Stage 3 — KPI Decomposition
- `decomposition/kpi_decomposition.py` — formula + dimensional contribution
- **Test**: decomposition correctly localizes the injected scenario to North/Electronics

### Stage 4 — Hypothesis Generation (rule-based first)
- `hypothesis/rules.py`
- **Test**: correct rules fire for the injected inventory-shortage scenario

### Stage 5 — Structured Evidence
- `evidence/structured_evidence.py`
- **Test**: returns correct stockout/availability figures for the affected segment

### Stage 6 — Unstructured Evidence (RAG)
- `evidence/retrieval.py` — embeddings + Chroma
- **Test**: planted warehouse-outage ticket retrieved in top-k for the relevant query

### Stage 7 — Confidence Engine
- `confidence/scoring.py`
- **Test**: known evidence combinations map to expected confidence labels

### Stage 8 — Ambiguity Gate
- `ambiguity/gate.py`
- **Test**: §4.5 (Ambiguity) scenario passes — competitor hypothesis correctly abstains

### Stage 9 — Recommendation Engine
- `recommendation/recommendation_engine.py` (Strong → full, Moderate → conditional, else None)
- **Test**: §4.7 (Recommendation Relevance) passes

### Stage 10 — LLM Layer
- `llm/llm_client.py`, `prompts.py`, `narrative.py` (provider-agnostic, per `09_LLM_ARCHITECTURE.md`)
- **Test**: §4.6 (Hallucination) and §4.10 (Persona Narrative) pass
- Enable `hypothesis/llm_suggest.py` behind config flag (per `13_BACKEND_MODULE_PLAN.md` §4 fix)

### Stage 11 — API Layer
- FastAPI app, auth (JWT), RLS session wiring, all endpoints (`11_SECURITY_AND_API.md` §4)
- **Test**: §4.9 (Role-Based Security) passes

### Stage 12 — Investigation Canvas UI
- Streamlit app per `12_FRONTEND_UX_PLAN.md`
- **Test**: manual walkthrough of all 7 UI sections against the MVP scenario

### Stage 13 — Feedback Loop
- `/feedback` endpoint + storage + summary view
- **Test**: §4.11 (Feedback Loop) passes

### Stage 14 — Telemetry
- `telemetry/logger.py`, `/telemetry/summary`, UI footer
- **Test**: telemetry entries created for every LLM call, cost estimate reasonable

### Stage 15 — End-to-End Testing
- Run all §4.1–§4.11 scenario tests together, in sequence, against the full pipeline
- Fix any integration-level issues surfaced only when modules run together

### Stage 16 — Packaging for Submission
- README (`18_README_PLAN` — approach, architecture, dependencies, execution instructions)
- Demo video recording
- Presentation deck (Round 1 template)
- Public GitHub repo push

## 3. Explicit Non-Sequential Note
LLM narrative (Stage 10) is deliberately built **after** Stages 2-9 are fully deterministic and
tested — this enforces the "build the reasoning before the storytelling" principle from the
Round 1 blueprint and avoids debugging LLM output against an unproven pipeline.

## 4. How Progress Is Tracked
`00_IMPLEMENTATION_PROGRESS.md` mirrors this exact stage list with checkboxes. A stage is
marked complete only when its listed test(s) pass — this file is what any model/session
(Claude, Gemini, Antigravity) reads first to know exactly where the build stands.