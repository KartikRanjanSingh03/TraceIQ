# TraceIQ — Data Sources & Reconciliation

## 1. Purpose
Round 2 explicitly requires handling "different source-system refresh cadences, grains, data
quality levels and historical coverage" and "inconsistent KPI definitions, hierarchies,
calendars, business rules and aggregation logic." This doc defines how TraceIQ reconciles the
four heterogeneous sources from `05_DATA_SCHEMA.md` into one common analysis-ready grain.

## 2. Source Summary (recap)
| Source | Table | Native grain | Refresh cadence | Quality level |
|---|---|---|---|---|
| Sales/Orders | `orders` | order-line | Daily batch (T-1) | High (system of record) |
| Inventory | `inventory_snapshots` | SKU-warehouse-day | Daily batch (T-1) | Medium (occasional missing snapshots) |
| Delivery | `deliveries` | shipment (event) | Hourly ingest | Medium (delayed timestamps possible) |
| Tickets/Notes | `tickets_notes` | event, irregular | Ad hoc | Low (unstructured, inconsistent tagging) |

## 3. Target Common Grain
All structured sources are reconciled to: **Date × Region × Category × Product × Channel ×
Customer Segment**, matching the dimension hierarchy in `04_KPI_SEMANTIC_CONTRACT.md` §4.
Unstructured sources are NOT forced into this grain — they retain their own timestamp/tags and
are linked to the common grain only at retrieval time (see §6).

## 4. Reconciliation Steps per Source

### 4.1 Orders → common grain
Already at order-line grain with all required dimensions. Aggregated via `GROUP BY` to produce
daily KPI values (Revenue, Orders, AOV) per dimension combination. No reconciliation needed
beyond aggregation.

### 4.2 Inventory → common grain
Native grain is SKU-warehouse-day; `warehouse_id` is mapped to `region` via a warehouse
lookup table. Missing daily snapshots (data quality gap) are forward-filled from the most
recent prior snapshot, with a `data_quality_flag = "forward_filled"` so downstream confidence
scoring can discount stale inventory evidence.

### 4.3 Deliveries → common grain
Event-level (per-shipment) records are aggregated by `dispatch_timestamp::date` and joined to
`region` to compute daily SLA compliance. Because ingestion is hourly, a delivery dispatched
late in the day may not be fully "settled" (delivered_timestamp null) at analysis time — these
are marked `in_transit` and excluded from same-day SLA calculations to avoid false positives.

### 4.4 Tickets/Notes → NOT force-aggregated
Kept at native event grain. Each document is tagged (where available) with `region`/
`category`/`product_id`; untagged documents remain retrievable via semantic search but are
not used for structured dimensional contribution analysis — only for evidence retrieval (RAG)
against a specific hypothesis. This avoids fabricating structure that isn't really there in the
data (a Round 2 -flagged real-world complexity).

## 5. Handling Different Refresh Cadences
The Change Detection layer runs once daily (T-1 batch complete for orders/inventory). At run
time, it checks each source's `source_freshness` timestamp (see `01_PRD.md` §6.3):
- If `deliveries` data is incomplete for the current day (still ingesting hourly), the pipeline
  uses the most recently completed day and flags partial-day data explicitly in the UI.
- If `inventory_snapshots` for a given day is missing, the forward-filled flag from §4.2 is
  surfaced as an evidence caveat, not hidden.

## 6. Linking Unstructured Evidence to a KPI Alert
When an alert fires for a specific Date × Region × Category (from Change Detection +
Decomposition), the Evidence Engine queries `tickets_notes` with a semantic search constrained
to a time window around the alert date and, where tagged, the matching region/category. This
is how unstructured, irregularly-timed data gets connected to a structured, dated KPI movement
without forcing it into the same rigid grain.

## 7. Inconsistent Definitions / Calendar Handling (MVP simplification)
For MVP, all sources use a single retail calendar (standard Gregorian, week starting Monday) —
avoiding fiscal-calendar or multi-calendar reconciliation complexity, which is called out as
out of scope in `01_PRD.md` §3. This is a deliberate MVP simplification, documented so it's
clear it was a conscious choice, not an oversight.

## 8. Data Quality Flags (carried downstream)
| Flag | Meaning | Effect on confidence scoring |
|---|---|---|
| `forward_filled` | Inventory snapshot missing, using prior day's value | Evidence weight discounted |
| `in_transit` | Delivery not yet settled | Excluded from same-day SLA calc |
| `partial_day` | Current day's batch incomplete | Alert marked provisional until next full run |
| `untagged` | Ticket/note has no region/category tag | Usable only via semantic retrieval, not structured contribution |