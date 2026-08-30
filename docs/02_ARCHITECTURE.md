# TraceIQ — System Architecture

## 1. Guiding Principle
The LLM sits at the *end* of the pipeline, not the start. Every stage before it produces
deterministic, testable, traceable output. The LLM only explains what has already been proven.

## 2. High-Level Pipeline
```
STRUCTURED DATA                 UNSTRUCTURED DATA
(orders, inventory,             (tickets, notes,
 delivery — different            reports — event-based)
 grains/cadences)
        │                              │
        └──────────── DATA LAYER ──────┘
                       │  (reconciled to common daily KPI grain)
              KPI SEMANTIC LAYER
                       │  (definitions, formulas, dimensions)
              CHANGE DETECTION
                       │  (baseline + z-score + persistence + materiality)
              KPI DECOMPOSITION
                       │  (contribution analysis: which dimension drove it)
              HYPOTHESIS ENGINE
                       │  (rule-based + LLM-suggested candidates)
              EVIDENCE ENGINE
                       │  (structured SQL evidence + unstructured RAG evidence)
              VALIDATION + CONFIDENCE ENGINE
                       │  (temporal, contribution, segment, contradiction checks)
              AMBIGUITY GATE
                       │  (High / Moderate / Insufficient)
              RECOMMENDATION ENGINE
                       │  (driver → lever → action → owner → monitoring KPI)
              LLM LAYER
                       │  (persona-specific narrative synthesis only)
              INVESTIGATION UI
                       │  (Investigation Canvas, persona views, security filtering)
              HUMAN DECISION
                       │  (accept / reject / request more evidence)
              FEEDBACK LOOP
                       (logged for future threshold/weight tuning)
```

## 3. Layer Responsibilities

### 3.1 Data Layer
Ingests structured sources (orders, inventory, delivery) and unstructured sources
(tickets/notes), reconciling different grains and refresh cadences to a common daily
KPI-ready grain. Owns data quality checks (nulls, freshness, duplicate detection).

### 3.2 KPI Semantic Layer
Central registry of KPI definitions, formulas (e.g. Revenue = Orders × AOV), dimensions,
thresholds, and access rules. No downstream layer computes a KPI without going through this
contract — see `04_KPI_SEMANTIC_CONTRACT.md`.

### 3.3 Change Detection
Determines if a KPI movement is meaningful using: historical baseline (rolling stats +
seasonality), statistical deviation (z-score), persistence (is it a one-off or a trend), and
business materiality (absolute ₹ impact, not just %). Outputs: NORMAL / WATCH / INVESTIGATE.

### 3.4 KPI Decomposition
Breaks a flagged KPI movement down mathematically (formula decomposition, then dimensional
contribution analysis by region/category/channel/segment) to localize the investigation before
any hypothesis is generated.

### 3.5 Hypothesis Engine
Generates candidate explanations using deterministic business rules first (e.g. "Orders↓ +
Stockouts↑ → Inventory hypothesis"), with LLM-assisted candidate suggestion as a secondary,
non-authoritative source. A hypothesis is never treated as a conclusion.

### 3.6 Evidence Engine
For each hypothesis, retrieves supporting/contradicting evidence: structured evidence via SQL
queries against the reconciled data layer, and unstructured evidence via semantic retrieval
(embedding + vector search) over tickets/notes/reports.

### 3.7 Validation + Confidence Engine
Scores each hypothesis on temporal alignment, contribution strength, segment consistency,
magnitude, independent evidence, and contradicting evidence. Produces a qualitative confidence
label (Strong / Moderate / Weak / Insufficient) — never an LLM-invented percentage.

### 3.8 Ambiguity Gate
Routes each hypothesis to one of three states: High Confidence → recommend action; Moderate →
conditional recommendation + human review; Insufficient → state explicitly what evidence is
missing and stop short of a claim.

### 3.9 Recommendation Engine
For sufficiently supported drivers, produces a structured recommendation: driver → controllable
lever → action → expected impact → owner → confidence → monitoring KPI. Never generic advice.

### 3.10 LLM Layer
Receives a verified evidence JSON package (not raw data) and produces the final natural-language
narrative, adapted per persona. Explicitly forbidden from: computing anomalies, doing KPI math,
inventing confidence, declaring causality independently, generating evidence, or making
autonomous decisions. See `09_LLM_ARCHITECTURE.md`.

### 3.11 Investigation UI
Presents the KPI alert, decomposition, evidence graph, hypothesis panel (with support AND
contradiction), and action card. Applies persona and security filtering. See
`12_FRONTEND_UX_PLAN.md`.

### 3.12 Human Decision + Feedback Loop
Every insight ends in a human action (accept/reject/edit/request more evidence). This decision
and eventual outcome is logged and used to refine hypothesis ranking, evidence weights, and
anomaly thresholds over time. See `14_FEEDBACK_AND_LEARNING_LOOP.md`.

## 4. Method Selection per Layer (why each technique is used)
| Layer | Method | Why not LLM |
|---|---|---|
| Change Detection | Rolling stats, z-score, seasonal decomposition | Needs deterministic, reproducible math |
| Decomposition | Arithmetic formula breakdown + groupby contribution | Exact, auditable, not probabilistic |
| Hypothesis rules | If/else business logic | Reliable, explainable, fast |
| Structured evidence | SQL queries | Ground truth from source data |
| Unstructured evidence | Embeddings + vector search (RAG) | Semantic matching over free text |
| Confidence scoring | Weighted rule-based scoring | Must be traceable/auditable, not a black box |
| Narrative | LLM | Best tool for fluent, persona-adapted natural language |

## 5. Cross-Cutting Concerns
- **Security**: row/column-level filtering applied before data reaches any layer (see `11_SECURITY_AND_API.md`)
- **Telemetry**: every LLM call and pipeline run logged with latency, tokens, cost (see `16_DEPLOYMENT_AND_TELEMETRY.md`)
- **Auditability**: every claim in the final narrative must be traceable to a lineage record