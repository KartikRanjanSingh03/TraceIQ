# TraceIQ — Product Requirements Document

## 1. Problem Statement
Dashboards show that a KPI moved (e.g. "Revenue down 8%") but rarely explain why, or what
to do about it. Translating a metric movement into an explanation and an action currently
requires a human analyst and takes days. TraceIQ is a KPI intelligence-to-action engine that:
- Detects material KPI movements (separating real change from normal noise)
- Explains *why* the movement happened, using both structured and unstructured data
- Assigns a confidence score to each explanation and abstains when evidence is weak
- Recommends a concrete next action, tied to the evidence
- Adapts the narrative to who is asking (persona-based)
- Learns from human feedback over time

## 2. Objectives (Round 2 scope)
1. Detect and prioritize material KPI movements
2. Reconcile data/business context across heterogeneous sources (different grains, refresh cadences)
3. Identify and rank explanatory drivers using appropriate methods (rules, stats, retrieval — LLM last)
4. Generate persona-specific narratives with traceable evidence
5. Communicate uncertainty; abstain when evidence is insufficient or contradictory
6. Recommend actions grounded in business levers and decision rights
7. Learn from analyst/business-user feedback
8. Operate within realistic security, cost, latency, and scalability constraints

## 3. Non-Goals (MVP)
- No autonomous execution of business actions (human approves everything)
- No production-grade multi-tenant security (one demo entitlement scenario only)
- No real-time (<1s) streaming — daily/hourly batch is sufficient
- No multi-agent orchestration / knowledge graphs — out of scope for MVP
- No Power BI integration in MVP (may be added post-MVP as a bonus layer)

## 4. Core Principle
**NO EVIDENCE → NO CONFIDENT STORY.**
The LLM never calculates anomalies, computes confidence, or declares causality on its own.
It only synthesizes and explains evidence that deterministic logic has already produced and
validated.

## 5. Target Personas (MVP: 2 minimum)
| Persona | Cares about | Narrative style |
|---|---|---|
| Business Leader (e.g. Regional Head) | Bottom-line impact, what to do | Short, action-first, minimal jargon |
| Analyst | Root cause detail, evidence trail, methodology | Detailed, evidence-linked, technical |

## 6. MVP Scope (single end-to-end scenario)
Domain: Retail / E-commerce.
KPIs: Revenue, Orders, AOV, Inventory Availability, Delivery SLA.
Scenario: Revenue ↓ 8.2%, driven by North-region Electronics order decline, caused by a
warehouse inventory outage (deliberately injected in synthetic data), with a secondary weak
signal (competitor activity) that should be marked "insufficient evidence."

### 6.1 Data Sources, Grains & Refresh Cadence (concrete)
| Source | Table | Grain | Refresh Cadence | Type |
|---|---|---|---|---|
| Sales/Orders system | `orders` | 1 row per order-line, per day | Daily batch (T-1) | Structured |
| Inventory/Warehouse system | `inventory_snapshots` | 1 row per SKU-warehouse, per day | Daily batch (T-1) | Structured |
| Delivery/Logistics system | `deliveries` | 1 row per shipment (event-level) | Near-real-time feed, ingested hourly | Structured |
| Support/Ops system | `tickets_notes` | 1 row per ticket/note (event-level, irregular) | Ad hoc / as raised | Unstructured (text) |

This deliberately mixes daily-aggregate grain (orders, inventory) with event-level grain
(deliveries, tickets) so the reconciliation layer must align different grains/cadences to a
common daily KPI grain before analysis — this is handled in the Data Layer (see
`06_DATA_SOURCES_AND_RECONCILIATION.md`).

### 6.2 Sparse-History Scenario (explicit)
Product: a newly launched SKU ("Electronics - Model X") in the North region, launched 18 days
before the analysis date (i.e., <30 days of history, no prior-year comparison possible).
Expected system behavior: standard seasonal/trend baseline cannot be computed reliably →
system falls back to a category-level or cross-region proxy baseline, and the confidence
engine explicitly downgrades confidence and states the limitation (e.g., "Limited history for
this SKU; comparison uses category-level baseline") rather than treating it as a normal
anomaly calculation.

### 6.3 Evidence Data Contract (explicit fields)
Every evidence item shown in the UI or passed to the LLM must carry these fields:
- `source_freshness`: timestamp of the underlying data as of the analysis run (e.g. "orders data as of 2026-08-29 23:00")
- `analytical_method`: which technique produced it (e.g. "z-score vs 90-day rolling baseline", "contribution decomposition", "semantic retrieval")
- `contribution`: quantitative share of the KPI movement this factor explains, where computable
- `confidence_label`: Strong / Moderate / Weak / Insufficient (qualitative, not a fabricated %)
- `lineage`: source table/document + query or retrieval reference, so any claim is traceable back to raw data

## 7. Minimum Prototype Expectations (from Round 2 brief)
- [ ] 3–5 connected KPIs across 2–3 data sources with different grains/refresh cadences
- [ ] Lightweight KPI/semantic contract (definitions, calculations, drivers, thresholds, lineage, access)
- [ ] ≥2 personas with different narratives/recommendations
- [ ] One multi-factor KPI movement with known/simulated drivers
- [ ] One low-confidence scenario where the engine abstains/asks for more data
- [ ] One sparse-history / newly launched KPI scenario
- [ ] One role-based security/entitlement scenario
- [ ] Evidence display: source freshness, method used, contribution, confidence, lineage
- [ ] Clear breakdown of LLM vs non-LLM processing steps
- [ ] Runtime telemetry: latency, model calls, token usage, estimated cost per insight

## 8. Success Criteria
The system runs end-to-end on the MVP scenario and:
1. Correctly flags the revenue drop as a meaningful anomaly (not noise)
2. Decomposes it to the right region/category
3. Generates 3–4 competing hypotheses
4. Retrieves both structured and unstructured evidence
5. Correctly ranks inventory shortage as the strongest driver
6. Correctly marks competitor activity as "insufficient evidence"
7. Produces a specific, evidence-linked recommendation (not generic advice)
8. Displays confidence, evidence, and lineage in the UI
9. Logs a human decision (accept/reject/request more evidence) to a feedback table
10. Correctly handles the sparse-history scenario (§6.2) with an honest confidence downgrade
11. Records runtime telemetry (latency, model calls, token usage, estimated cost) for every
    insight generated, visible in the UI or logs

## 9. Deliverables (final submission)
- Public GitHub repo (code + docs)
- README: approach, architecture, dependencies, execution instructions
- Demo video showing the end-to-end scenario
- Presentation deck (Round 1 template reused)