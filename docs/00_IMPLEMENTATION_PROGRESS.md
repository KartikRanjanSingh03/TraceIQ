# TraceIQ — Implementation Progress

This is the single source of truth for build status. Any AI agent picking up this project
reads this file first, finds the first unchecked task, and works on only that task.
See `CLAUDE.md` for the rules governing how work is done, and `17_ROADMAP.md` for full detail
on each stage.

## Documentation (Planning Phase)
- [x] 01_PRD.md
- [x] 02_ARCHITECTURE.md
- [x] 03_TECH_STACK.md
- [x] 04_KPI_SEMANTIC_CONTRACT.md
- [x] 05_DATA_SCHEMA.md
- [x] 06_DATA_SOURCES_AND_RECONCILIATION.md
- [x] 07_ANALYTICS_AND_DRIVER_ANALYSIS.md
- [x] 08_CONFIDENCE_AND_ABSTENTION.md
- [x] 09_LLM_ARCHITECTURE.md
- [x] 10_DATABASE_DESIGN.md
- [x] 11_SECURITY_AND_API.md
- [x] 12_FRONTEND_UX_PLAN.md
- [x] 13_BACKEND_MODULE_PLAN.md
- [x] 14_FEEDBACK_AND_LEARNING_LOOP.md
- [x] 15_TESTING_STRATEGY.md
- [x] 16_DEPLOYMENT_AND_TELEMETRY.md
- [x] 17_ROADMAP.md
- [x] CLAUDE.md

**Documentation phase: COMPLETE.** All planning docs reviewed and corrected. Build phase begins next.

## Build Phase (per 17_ROADMAP.md)

### Stage 0 — Project Setup ✅
- [x] Repo structure created
- [x] Postgres running locally (native PostgreSQL 18 service; docker-compose.yml also provided for Docker-based setup)
- [x] requirements.txt, .env.example
- [x] Test: DB connects, empty schema created

> **Status (2026-08-30):** 3/3 pytest tests passed (`test_db_ping`, `test_schemas_created`, `test_no_application_tables_yet`). Schemas `raw`, `kpi`, `pipeline` created in local Postgres 18. Ready for Stage 1.

### Stage 1 — Synthetic Dataset + KPI Semantic Contract
- [x] generate_synthetic_data.py written
- [x] kpi_contract.yaml + kpi_contract.py written
- [x] DB migrations + KPI views created (001_raw_tables, 002_kpi_views, 003_pipeline_tables, 004_rls_policies)
- [x] Test: data loads, views return expected aggregates

> **Status (2026-08-30):** 25/25 pytest tests passed. 383,614 orders + 4,339 inventory + 60,033 delivery + 83 ticket rows loaded. KPI view confirmed North Electronics revenue depressed vs South on analysis date. Outage, sparse-history SKU, and competitor signal all verified. Ready for Stage 2.

### Stage 2 — Change Detection ✅
- [x] change_detection.py written
- [x] Test: Noise + True Anomaly scenarios pass

> **Status (2026-08-30):** 15/15 pytest tests passed. Noise: 4 normal grains + pre-outage day confirmed NORMAL. True Anomaly: North/Electronics → INVESTIGATE (z<<-2, persistence≥2, impact>₹1L). Persistence check, sparse-history flag, and category-proxy fallback all verified. Ready for Stage 3.

### Stage 3 — KPI Decomposition ✅
- [x] kpi_decomposition.py written
- [x] Test: correctly localizes injected scenario

> **Status (2026-08-30):** 19/19 pytest tests passed. Level 1: Orders identified as dominant factor (multiplicative attribution; Orders revenue-equiv contribution >> AOV). Level 2: channel/segment contributions sum to ~1.0. End-to-end: North grain → Electronics as top category contributor. Ready for Stage 4.

### Stage 4 — Hypothesis Generation (rules) ✅
- [x] rules.py written
- [x] Test: correct rules fire for inventory-shortage scenario

> **Status (2026-08-30):** 14/14 pytest tests passed. R01 (inventory shortage) fires and tops the ranking (signal_strength>0.5). R03 (pricing) and R06 (demand decline) correctly do not fire. R04 (competitor) fires but is weaker than R01. Normal grain → zero rules fire. Ready for Stage 5.

### Stage 5 — Structured Evidence ✅
- [x] structured_evidence.py written
- [x] Test: returns correct stockout/availability figures for the affected segment

> **Status (2026-08-30):** 25/25 pytest tests passed. R01: stockout_rate ~100% (baseline <1%), delta >80pp, stock~0, control region (South) <10% confirms region-specific. R03 discount delta <5pp (not supporting — no pricing change injected). R04: planted competitor ticket found (market-wide search, not region-filtered). Ready for Stage 6.

### Stage 6 — Unstructured Evidence (RAG)
- [x] retrieval.py written (embeddings + Chroma)
- [x] Test: planted document retrieved in top-k

> **Status (2026-08-30):** 16/16 pytest tests passed. 83 docs indexed (all-MiniLM-L6-v2). PLANTED-OUTAGE-001 retrieved with score ≥0.40, tagged Supporting. Competitor doc filtered out for outage query (semantic discrimination verified). R04 market-wide retrieval finds PLANTED-COMPETITOR-001. Ready for Stage 7.

### Stage 7 — Confidence Engine ✅
- [x] scoring.py written
- [x] Test: known evidence maps to expected confidence labels

> **Status (2026-08-30):** 15/15 pytest tests passed. Strong: full inventory evidence (signal 0.95 + 3 supporting + RAG 0.75). Insufficient/Weak: competitor (signal 0.30 + contradicting items + no RAG). Sparse history: penalty applied, Strong label hard-capped to Moderate. score_all: R01 ranked above R04. Ready for Stage 8.

### Stage 8 — Ambiguity Gate ✅
- [x] gate.py written
- [x] Test: Ambiguity scenario abstains correctly

> **Status (2026-08-30):** 20/20 pytest tests passed. Competitor alone → Insufficient. Inventory Strong → HighConfidence. Mixed (R01 Strong + R04 Insufficient) → HighConfidence overall (per-hypothesis confidence preserved). Near-equal Moderate hypotheses (gap<0.10) → Insufficient (ambiguity). Abstention template has all 5 required fields. Ready for Stage 9.

### Stage 9 — Recommendation Engine ✅
- [x] recommendation_engine.py written (Strong/Moderate/else branches)
- [x] Test: Recommendation Relevance scenario passes

> **Status (2026-08-30):** 17/17 pytest tests passed. Full (HighConfidence/R01): supply chain action owner, replenishment immediate action. Conditional (Moderate): condition_for_full + human_review_note. None (Insufficient): "no action recommended". is_actionable correct for all 3 tiers. Ready for Stage 10.

### Stage 10 — LLM Layer ✅
- [x] llm_client.py (provider-agnostic), prompts.py, narrative.py written
- [x] llm_suggest.py added behind config flag
- [x] Test: Hallucination + Persona Narrative scenarios pass

> **Status (2026-08-30):** 19/19 pytest tests passed. GUARDRAIL marker injected for zero-evidence hypotheses; inventory evidence has no guardrail. business_leader: no z-score jargon; analyst: evidence trail in system prompt. Caching: second call returns cached=True. llm_suggest disabled by default returns []. Ready for Stage 11.

### Stage 11 — API Layer ✅
- [x] FastAPI app, auth, RLS session wiring, endpoints built
- [x] Test: Role-Based Security scenario passes

> **Status (2026-08-30):** 19/19 pytest tests passed. JWT login (sha256_crypt), wrong password → 401, unauthenticated → 401. regional_leader: 403 on /pipeline/run and South alert, 200 on own North alert. analyst: 200 everywhere. /telemetry/summary blocked for leader. Feedback tags submitter role. Ready for Stage 12.

### Stage 12 — Investigation Canvas UI ✅
- [x] Streamlit app built (all 7 sections)
- [x] Manual walkthrough against MVP scenario passes

> **Status (2026-08-30):** End-to-end walkthrough: INVESTIGATE, pct=−73.4%, z=−3.18. Dominant=Orders. Top hyp=Inventory shortage (Moderate, sparse-history cap). Gate=Moderate. Rec=Conditional. Narrative generated (mock). UI: 7 sections with Plotly charts, confidence badges, evidence cards, hypothesis panel, LLM narrative, action card with feedback buttons, analyst-only telemetry footer. Ready for Stage 13.

### Stage 13 — Feedback Loop ✅
- [x] /feedback endpoint + storage + summary view built
- [x] Test: Feedback Loop scenario passes

> **Status (2026-08-30):** 8/8 feedback tests passed. submit() persists to feedback_log (PostgreSQL SERIAL PK, RETURNING id). get_feedback() retrieves by alert_id. get_summary() counts by decision + accept_rate. Invalid decision raises ValueError. All 4 valid decisions accepted. Entries tagged with role + timestamp. Ready for Stage 14.

### Stage 14 — Telemetry ✅
- [x] logger.py, /telemetry/summary, UI footer built
- [x] Test: telemetry entries created per LLM call

> **Status (2026-08-30):** 7/7 telemetry tests passed. log_llm_call() inserts into telemetry_log (PostgreSQL SERIAL). get_summary() returns total_calls, cached_calls, avg_latency_ms, total_tokens, estimated_cost_usd. §4.14 scenario: 3 LLM calls → count increases by exactly 3. Mock cost = $0.00. Ready for Stage 15.

### Stage 15 — End-to-End Testing ✅
- [x] All scenario tests (§4.1–§4.11 in 15_TESTING_STRATEGY.md) run together and pass

> **Status (2026-08-30):** 33/33 pytest tests passed (7m 12s). §4.1 Noise: NORMAL grain confirmed no INVESTIGATE. §4.2 True Anomaly: INVESTIGATE triggered, z=−3.18, persistence≥2. §4.3 Root-Driver: Inventory shortage top hypothesis (Moderate, sparse-history cap). §4.4 Contradiction: Competitor weaker than Inventory. §4.5 Ambiguity: near-equal Moderate gap→Insufficient+missing_data. §4.6 Hallucination: GUARDRAIL injected for zero-evidence pricing hypothesis. §4.7 Recommendation: inventory/replenishment terms confirmed, not generic. §4.8 Sparse-History: category_proxy baseline, Moderate cap, notes populated. §4.9 Role-Based Security: regional_leader blocked from South (403), unauthenticated blocked (401). §4.10 Persona Narrative: both generated, differ in text, no z-score jargon in BL. §4.11 Feedback Loop: all 4 decisions stored with role+timestamp, summary reflects decisions. Integration fix: starlette pinned to >=0.37.2,<0.39.0 (streamlit 1.62 had shipped starlette 1.6 which broke FastAPI TestClient). SUBMISSION-READY.

### Stage 16 — Packaging for Submission
- [ ] README written
- [ ] Demo video recorded
- [ ] Presentation deck updated (Round 1 template)
- [ ] Public GitHub repo pushed

## Notes / Decisions Log
(Add a dated one-line entry here whenever a design decision is made or changed during the
build that isn't already captured in `docs/`.)