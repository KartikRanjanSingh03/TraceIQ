# TraceIQ — Technology Stack

## 1. Principle
Choose the smallest, most reliable stack that proves the architecture end-to-end. No tool is
added "to look impressive" — every choice maps to a specific layer in `02_ARCHITECTURE.md`.

## 2. Stack by Layer

| Layer | Technology | Why |
|---|---|---|
| Data processing | Python 3.11 + Pandas | Standard, fast to iterate, huge ecosystem |
| Database | PostgreSQL | Real SQL semantics, row/column security supportable, free, production-credible |
| Statistics / anomaly detection | NumPy + statsmodels + scikit-learn | Rolling stats, z-scores, seasonal decomposition — no need for deep learning here |
| Unstructured retrieval | Sentence-Transformers + ChromaDB | Local, free, simple embedding + vector search, no external infra needed |
| Backend / API | FastAPI | Async, typed, fast to build REST endpoints, auto docs (OpenAPI) |
| LLM | Claude API (cheapest tier, e.g. Haiku) OR free-tier alternative (e.g. Gemini free tier) | Narrative layer only needs fluent language generation from a pre-built evidence package, not heavy reasoning — a small/cheap or free-tier model is sufficient; architecture (§3.10 in `02_ARCHITECTURE.md`) doesn't change regardless of which model is plugged in |
| Frontend (prototype) | Streamlit | Fastest way to build the Investigation Canvas UI without a separate frontend build step |
| Frontend (stretch/polish) | React + FastAPI | Only if time remains post-MVP |
| Visualization | Plotly | Interactive charts inside Streamlit (expected vs actual, decomposition tree) |
| Scheduling | Python `schedule` lib or simple cron | Simulates the "daily/hourly" refresh cadence without needing Airflow |
| Feedback storage | PostgreSQL table (`feedback_log`) | Same DB, no extra infra |
| Telemetry | Custom logging table + `time` + provider-specific API usage metadata (works for Claude or Gemini) | Captures latency, token counts, estimated cost per call regardless of which LLM provider is plugged in |
| Version control / submission | Git + GitHub (public repo) | Required deliverable format |

## 3. Explicitly NOT Used in MVP (and why)
| Not used | Reason |
|---|---|
| Airflow / Prefect | Overkill for a single scheduled daily job in MVP |
| Kafka / streaming infra | No true real-time requirement; batch is sufficient and expected |
| Knowledge graph DB (Neo4j) | Adds complexity with no MVP benefit; simple relational lineage is enough |
| Multi-agent frameworks (LangGraph/CrewAI) | Adds orchestration complexity; a linear deterministic pipeline is clearer and more auditable |
| Power BI / Tableau | Optional bonus layer post-MVP, not core to proving the engine |
| Cloud-managed vector DB (Pinecone) | Local Chroma is sufficient for prototype scale, free, no external dependency |
| Kubernetes / container orchestration | Single-process prototype; Docker Compose is enough if containerization is needed at all |

## 4. Environment
- Python 3.11+, virtualenv or `uv`
- PostgreSQL 15+ (local Docker container is fine)
- `.env` for API keys (LLM provider key, DB connection string) — never committed to git
- `requirements.txt` pinned versions for reproducibility

## 5. Why This Stack Meets Round 2's "Cost/Latency/Scalability" Constraint
- Everything is open-source/self-hosted except the LLM call itself, so infra cost ≈ $0 for the prototype
- The only paid, rate-limited resource is the LLM API — isolated to one layer, easy to log and cap
- Postgres + Chroma scale far beyond MVP data volumes without changes; a production version would
  swap Postgres for a warehouse (Snowflake/BigQuery) and Chroma for a managed vector DB, but the
  architecture (layers, contracts, pipeline) does not need to change — only the connection layer does

## 6. Zero-Budget Constraint
This project targets $0 total cost for the prototype:
- All infra components (§2, excluding LLM) are free/self-hosted
- LLM narrative layer uses either free trial credits or a free-tier model — swappable without any
  architecture change, since the LLM only ever receives a small, pre-built evidence JSON (cheap
  in tokens) rather than raw data, keeping usage minimal regardless of provider
- Postgres and Chroma run locally in Docker — no cloud hosting fees during development
- If a hosted demo is needed for submission, free tiers (e.g. Render/Railway/Streamlit Community
  Cloud, if available on their current free plans at submission time) are sufficient at prototype
  scale — local run + demo video remains the fallback if free hosting limits change