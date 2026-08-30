# TraceIQ — Deployment & Telemetry

## 1. Purpose
Covers how the prototype is run/deployed (zero-budget, per `03_TECH_STACK.md`) and how
runtime telemetry (latency, model calls, token usage, estimated cost) is captured and
displayed — a required Round 2 minimum expectation.

## 2. Deployment Model (MVP)
- **Local-first**: Postgres (Docker), FastAPI backend, Streamlit UI, all run locally during
  development and for the demo video/live walkthrough.
- **Optional hosted demo**: if free-tier hosting is available at submission time (e.g. Render/
  Railway for FastAPI+Postgres, Streamlit Community Cloud for the UI), deploy there — hedged
  in `03_TECH_STACK.md` §5 since free-tier availability/limits can change.
- **Fallback**: a recorded demo video walking through the local run is always the guaranteed
  deliverable, independent of hosting availability (per submission requirements).

## 3. Environment Configuration
`.env` file (not committed) holding:
```
DATABASE_URL=postgresql://user:pass@localhost:5432/traceiq
LLM_PROVIDER=claude   # or "gemini"
ANTHROPIC_API_KEY=...  # if using Claude
GEMINI_API_KEY=...     # if using Gemini
JWT_SECRET=...
```
`.env.example` committed with placeholder values, per standard practice.

## 4. Runtime Telemetry — What's Captured
Per `05_DATA_SCHEMA.md` §3.6 (`telemetry_log`), every LLM call logs:
| Field | Source |
|---|---|
| `latency_ms` | Wall-clock time around the `llm_client.generate()` call |
| `input_tokens` / `output_tokens` | Provider-specific API usage metadata (works for Claude or Gemini, per earlier correction) |
| `estimated_cost_usd` | Computed from token counts × the active provider's published per-token rate (config-driven, updatable) |
| `llm_provider` | Which provider handled this call |
| `alert_id` | Links back to the specific KPI investigation that triggered the call |

## 5. Telemetry Display
Surfaced via `/telemetry/summary` (analyst/admin only, per `11_SECURITY_AND_API.md` §5) and
the UI's Telemetry Footer (`12_FRONTEND_UX_PLAN.md` §3.3, Section 7). Shows: total LLM calls
made, average latency, total tokens consumed, total estimated cost — both per-alert and
aggregated across the demo session.

## 6. Latency Budget (design target, not a hard SLA for MVP)
| Stage | Target |
|---|---|
| Change detection + decomposition | < 1s (pure computation on aggregated data) |
| Evidence retrieval (structured + RAG) | < 2s |
| Confidence scoring + ambiguity gate | < 0.5s |
| LLM narrative generation (per persona) | 2-5s (external API call, dominant latency source) |
| **Total per alert (both personas)** | **~5-10s** |
This is documented as a design target to show latency was considered, not a benchmarked
production guarantee.

## 7. Cost Control (recap, ties to `09_LLM_ARCHITECTURE.md` §9)
- Small, structured evidence-package input → low token count per call
- One call per persona per alert (not per hypothesis) → bounded volume
- Response caching on (alert_id, persona) → avoids duplicate spend during demo/testing
- Rate limiting on `/pipeline/run` → prevents runaway cost from accidental repeated triggers

## 8. Scalability Note (honest MVP framing)
The MVP is built and tested at synthetic-dataset scale (thousands of rows). §10 of
`10_DATABASE_DESIGN.md` and §10 of `03_TECH_STACK.md` already describe the production-scale
migration path (warehouse + managed vector DB) without changing the architecture — this doc
does not re-claim untested production-scale performance, only documents the design intent.

## 9. Monitoring & Drift (stated direction, MVP-scoped)
Round 2 mentions "model and data drift" as a real-world complexity. For MVP: telemetry logging
(§4) plus the feedback loop (`14_FEEDBACK_AND_LEARNING_LOOP.md`) form the foundation drift
monitoring would build on (e.g. tracking confidence-label accuracy over time via feedback
outcomes) — full automated drift detection is explicitly out of scope for the prototype, named
here so it's a disclosed boundary, not a gap discovered later.