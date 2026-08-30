# TraceIQ — Data Schema

## 1. Purpose
Concrete table-level schema for the synthetic MVP dataset. Every column here maps back to a
KPI or driver defined in `04_KPI_SEMANTIC_CONTRACT.md`. This is what gets loaded into
PostgreSQL and queried by every downstream layer.

## 2. Structured Tables

### 2.1 `orders` (grain: 1 row per order-line, daily batch)
| Column | Type | Notes |
|---|---|---|
| order_id | TEXT | NOT unique (grain is order-line; same order_id repeats across lines). Indexed for joins, not a PK. |
| order_line_id | TEXT | PK |
| date | DATE | |
| region | TEXT | North/South/East/West |
| category | TEXT | Electronics/Apparel/Home |
| product_id | TEXT | SKU |
| channel | TEXT | Online/Marketplace/In-store |
| customer_segment | TEXT | New/Returning/Premium |
| units | INT | |
| price | NUMERIC | unit price |
| discount_pct | NUMERIC | 0-1 |
| revenue | NUMERIC | units × price × (1-discount_pct) |
| product_launch_date | DATE | used for sparse-history detection |

### 2.2 `inventory_snapshots` (grain: 1 row per SKU-warehouse, daily batch)
| Column | Type | Notes |
|---|---|---|
| snapshot_id | TEXT | PK |
| date | DATE | |
| region | TEXT | maps to warehouse region |
| category | TEXT | |
| product_id | TEXT | SKU |
| warehouse_id | TEXT | |
| stock_on_hand | INT | |
| stockout_flag | BOOLEAN | true if stock_on_hand = 0 |
| replenishment_lead_time_days | INT | |

### 2.3 `deliveries` (grain: 1 row per shipment, event-level, hourly ingest)
| Column | Type | Notes |
|---|---|---|
| delivery_id | TEXT | PK |
| order_id | TEXT | FK → orders |
| dispatch_timestamp | TIMESTAMP | |
| delivered_timestamp | TIMESTAMP | nullable if in transit |
| region | TEXT | |
| promised_sla_hours | INT | |
| actual_delivery_hours | NUMERIC | nullable until delivered |
| sla_breach_flag | BOOLEAN | |

### 2.4 `tickets_notes` (grain: 1 row per ticket/note, event-level, irregular/ad hoc)
| Column | Type | Notes |
|---|---|---|
| doc_id | TEXT | PK |
| timestamp | TIMESTAMP | |
| region | TEXT | nullable if not region-specific |
| category | TEXT | nullable |
| product_id | TEXT | nullable |
| source | TEXT | e.g. "support_ticket", "ops_report", "market_note" |
| text | TEXT | free-text content, embedded for retrieval |

## 3. System Tables (not business data — pipeline state)

### 3.1 `kpi_alerts` (output of Change Detection layer)
| Column | Type | Notes |
|---|---|---|
| alert_id | TEXT | PK |
| kpi_name | TEXT | |
| date | DATE | |
| grain_filters | JSONB | e.g. {"region":"North","category":"Electronics"} |
| actual_value | NUMERIC | |
| expected_value | NUMERIC | |
| zscore | NUMERIC | |
| persistence_days | INT | |
| business_impact_inr | NUMERIC | |
| status | TEXT | NORMAL / WATCH / INVESTIGATE |

### 3.2 `hypotheses` (output of Hypothesis + Evidence + Confidence layers)
| Column | Type | Notes |
|---|---|---|
| hypothesis_id | TEXT | PK |
| alert_id | TEXT | FK → kpi_alerts |
| driver | TEXT | e.g. "Inventory shortage" |
| confidence_label | TEXT | Strong/Moderate/Weak/Insufficient |
| supporting_evidence | JSONB | list of evidence objects (see §3.3) |
| conflicting_evidence | JSONB | list of evidence objects |
| generated_by | TEXT | "rule" or "llm_suggested" |

### 3.3 Evidence object shape (stored inside `hypotheses.supporting_evidence` / `conflicting_evidence`)
```json
{
  "description": "Stockouts increased 17% in North Electronics",
  "source_table_or_doc": "inventory_snapshots",
  "analytical_method": "contribution decomposition",
  "contribution": 0.42,
  "source_freshness": "2026-08-29T23:00:00",
  "lineage_query_ref": "query_id_or_sql_hash"
}
```

### 3.4 `recommendations`
| Column | Type | Notes |
|---|---|---|
| recommendation_id | TEXT | PK |
| hypothesis_id | TEXT | FK → hypotheses |
| controllable_lever | TEXT | e.g. "Replenishment priority" |
| action | TEXT | |
| expected_impact | TEXT | |
| owner_role | TEXT | e.g. "Regional Ops Manager" |
| monitoring_kpi | TEXT | |

### 3.5 `feedback_log`
| Column | Type | Notes |
|---|---|---|
| feedback_id | TEXT | PK |
| recommendation_id | TEXT | FK → recommendations |
| user_id | TEXT | ID of the user submitting feedback |
| user_role | TEXT | |
| decision | TEXT | accept/reject/edit/request_more_evidence |
| comment | TEXT | nullable |
| timestamp | TIMESTAMP | |
| actual_outcome | TEXT | nullable, filled in later |

### 3.6 `telemetry_log`
| Column | Type | Notes |
|---|---|---|
| call_id | TEXT | PK |
| alert_id | TEXT | FK → kpi_alerts |
| llm_provider | TEXT | e.g. "claude" / "gemini" |
| latency_ms | INT | |
| input_tokens | INT | |
| output_tokens | INT | |
| estimated_cost_usd | NUMERIC | |
| timestamp | TIMESTAMP | |

## 4. Relationships Summary
```
orders ──┐
inventory_snapshots ──┼──> kpi_alerts ──> hypotheses ──> recommendations ──> feedback_log
deliveries ──┤                              │
tickets_notes ──┘                            └──> telemetry_log (per LLM call)

kpi_alerts ──> telemetry_log   (each pipeline run/alert generates its own telemetry entries)
```

**Notes on relationships:**
- `orders.order_id` is many-to-one from `deliveries.order_id` (an order can have multiple
  lines, one delivery per order). In MVP, this is a **logical relationship only** — no strict
  FK constraint enforced, since `orders` grain is order-line, not order-level. A separate
  `orders_header` table would be needed for a true FK; not required for MVP.
- `telemetry_log` is tied to `kpi_alerts` (one alert/run can trigger multiple LLM calls, each
  logged separately), not directly chained off `hypotheses` — shown as a parallel branch above.

## 5. Sparse-History Handling (schema-level)
`orders.product_launch_date` is the field the Change Detection layer checks: if
`date - product_launch_date < 30`, the baseline calculation falls back to category-level
proxy data instead of SKU-level history (implements `01_PRD.md` §6.2).
