# TraceIQ — Analytics & Driver Analysis

## 1. Purpose
Defines the concrete analytical methods used to (a) detect meaningful KPI movement, (b)
decompose it, (c) generate hypotheses, and (d) validate them into a ranked, confidence-scored
list of drivers. This is the deterministic core of the system — no LLM involved anywhere in
this document.

## 2. Change Detection (Meaningful vs Noise)

### 2.1 Expected Baseline
For each KPI × dimension combination, compute an expected value using:
- **Rolling mean/std** over a trailing window (default: 90 days), excluding known outliers
- **Day-of-week seasonality adjustment** (e.g. weekends behave differently from weekdays)
- **Comparable period** (same weekday, 1/4 weeks prior) as a secondary check

### 2.2 Statistical Deviation
```
z = (Actual - Expected) / Historical_StdDev
```
`|z| > 2` (configurable per KPI, see `04_KPI_SEMANTIC_CONTRACT.md` §5) marks a candidate anomaly.

### 2.3 Persistence Check
A single day crossing the z-score threshold is marked `WATCH`. The same direction of deviation
persisting ≥2 consecutive days escalates it to `INVESTIGATE`. This prevents single-day noise
from triggering a full investigation.

### 2.4 Business Materiality
Statistical significance alone is not sufficient. The absolute ₹ (or unit) impact is compared
against the materiality threshold (`04_KPI_SEMANTIC_CONTRACT.md` §5). A movement must cross
**both** the statistical and materiality thresholds to reach `INVESTIGATE`.

### 2.5 Output States
`NORMAL` (ignore) → `WATCH` (monitor, log only) → `INVESTIGATE` (triggers full pipeline).

### 2.6 Sparse-History Adjustment
If the affected product/SKU has < 30 days of history (`orders.product_launch_date` check per
`05_DATA_SCHEMA.md` §5), the baseline falls back to the category-level average as a proxy, and
the resulting alert is tagged `low_history_confidence` — this flows into the Confidence Engine
(§5) as an automatic confidence ceiling (max: Moderate, never Strong).

## 3. KPI Decomposition

### 3.1 Formula Decomposition (Level 1)
Using the KPI formulas from `04_KPI_SEMANTIC_CONTRACT.md` §3, e.g.:
```
Revenue = Orders × AOV
```
Compute each factor's % change and its approximate contribution to the total KPI % change
(via log-decomposition or simple multiplicative attribution) to identify which factor
(Orders vs AOV) is primarily responsible.

### 3.2 Dimensional Contribution Analysis (Level 2)
For the dominant factor (e.g. Orders), decompose by each dimension (Region, Category, Channel,
Customer Segment) using contribution-to-variance:
```
contribution(segment) = (segment_actual - segment_expected) / (total_actual - total_expected)
```
Rank segments by absolute contribution to localize the investigation (e.g. "North + Electronics
= 68% of the total Orders decline").

### 3.3 Output
A localized problem statement: *"Why did North-region Electronics orders decline?"* instead of
*"Why did total revenue decline?"* — this narrows the hypothesis and evidence search space
significantly (per the Round 1 blueprint's reasoning).

## 4. Hypothesis Generation

### 4.1 Rule-Based (primary, authoritative)
Deterministic if/else rules mapping observed co-occurring signals to candidate drivers:
| Condition | Hypothesis |
|---|---|
| Orders↓ AND Stockouts↑ (inventory_snapshots) | Inventory shortage |
| Orders↓ AND SLA_breach↑ (deliveries) | Delivery disruption |
| Orders↓ AND avg price↑ / discount↓ (orders) | Pricing effect |
| Orders↓ AND no supply/pricing/delivery signal | Demand decline (residual) |
| Orders↓ AND external ticket mentions competitor | Competitor activity (candidate only) |
| Orders↓ AND customer_segment mix shifted | Customer/product mix change |

### 4.2 LLM-Assisted (secondary, non-authoritative)
The LLM may be given the business context (KPI alert + decomposition, NOT raw data) and asked
to suggest *additional* candidate hypotheses the rules didn't cover. These are appended to the
candidate list with `generated_by: "llm_suggested"` and go through the **same** evidence
validation as rule-based hypotheses — an LLM-suggested hypothesis is never treated with more
trust and never skips validation.

## 5. Evidence Retrieval

### 5.1 Structured Evidence (SQL)
For each hypothesis, run a targeted query against the relevant reconciled table (e.g. for
Inventory shortage: query `inventory_snapshots` for stockout rate and availability change in
the localized segment/window).

### 5.2 Unstructured Evidence (RAG)
Semantic search over `tickets_notes` (per `06_DATA_SOURCES_AND_RECONCILIATION.md` §6),
constrained to the alert's time window and, where tagged, region/category. Pipeline:
`document → chunk → embed (Sentence-Transformers) → store (Chroma) → semantic query at
retrieval time`. Top-k relevant snippets returned as evidence candidates.

## 6. Evidence Validation (Correlation → Confidence)

Each hypothesis is scored on these dimensions (all deterministic, no LLM):

| Dimension | Question | How computed |
|---|---|---|
| Temporal alignment | Did the driver's signal move at/before the KPI movement? | Compare date offsets |
| Contribution strength | How much of the KPI change does this factor explain? | From §3.2 contribution calc |
| Segment consistency | Is the driver's signal concentrated in the same segment as the KPI movement? | Compare affected dimensions |
| Magnitude | Did the driver itself move meaningfully? | z-score / % change on the driver metric |
| Independent evidence | Do multiple independent sources agree? | Count distinct source tables/docs supporting it |
| Contradicting evidence | Is there evidence against this hypothesis? | Explicit check (e.g. unaffected comparable segment) |

### 6.1 Confidence Formula (conceptual)
```
confidence_score = f(temporal, contribution, segment_consistency, magnitude, independent_evidence) - contradiction_penalty
```
Implemented as a **weighted rule-based scoring system** (not ML, not LLM) for MVP — weights are
config-driven and documented, not arbitrary black-box numbers.

### 6.2 Confidence Labels (qualitative, not fabricated %)
| Score range | Label |
|---|---|
| High, all dimensions pass, no contradictions | Strong |
| Most dimensions pass, minor gaps | Moderate |
| Weak signal or unresolved contradiction | Weak |
| Missing key evidence, or history too sparse | Insufficient |

Numeric scores are used internally for ranking only; the UI and LLM only ever see the
qualitative label plus the underlying evidence — never an invented precision percentage.

## 7. Evidence Tiers (Correlation → Actionable Insight)
| Tier | Meaning |
|---|---|
| 1 — Observed relationship | Two signals moved together; no causal claim |
| 2 — Likely contributor | Timing + segment + independent evidence align |
| 3 — Stronger causal support | Where data permits: before/after or treatment-vs-control comparison (stretch goal, not MVP-required) |
| 4 — Insufficient evidence | Data does not support a reliable conclusion |

Language policy (enforced in the LLM layer, `09_LLM_ARCHITECTURE.md`): prefer "observed
relationship," "likely contributor," "strongly supported driver," "insufficient evidence" —
never "X caused Y" unless Tier 3 evidence exists.