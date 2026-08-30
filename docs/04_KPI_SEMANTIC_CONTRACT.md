# TraceIQ — KPI Semantic Contract

## 1. Purpose
No layer in the system (detection, decomposition, LLM, UI) is allowed to compute or interpret
a KPI outside of this contract. This is the single source of truth for what each KPI means,
how it's calculated, what drives it, and who can see it. This directly implements Round 2's
requirement: "a lightweight KPI or semantic contract covering definitions, calculations,
drivers, thresholds, lineage and access restrictions."

## 2. Domain
Retail / E-commerce (MVP scope, per `01_PRD.md`).

## 3. KPI Definitions

### 3.1 Revenue
- **Formula**: `Revenue = Orders × AOV`
- **Grain**: Daily, by Region / Category / Product / Channel / Customer Segment
- **Source table**: `orders`
- **Unit**: ₹ (INR)
- **Drivers**: Order volume, pricing/discounting, product mix, customer segment mix

### 3.2 Orders
- **Formula**: `Orders = COUNT(distinct order_id)`
- **Grain**: Daily, by Region / Category / Channel / Customer Segment
- **Source table**: `orders`
- **Drivers**: Demand, inventory availability, delivery reliability, marketing/promotions, competitor activity

### 3.3 Average Order Value (AOV)
- **Formula**: `AOV = Revenue / Orders`
- **Grain**: Daily, by Region / Category / Channel
- **Source table**: `orders`
- **Drivers**: Pricing, discounting, product mix, upsell/cross-sell effectiveness

### 3.4 Inventory Availability
- **Formula**: `Availability = 1 - (Stockout_SKU_Days / Total_SKU_Days)`
- **Grain**: Daily, by Region / Category / Product
- **Source table**: `inventory_snapshots`
- **Drivers**: Warehouse supply, replenishment lead time, demand forecasting accuracy

### 3.5 Delivery SLA
- **Formula**: `SLA_Compliance = 1 - (SLA_Breaches / Total_Deliveries)`
- **Grain**: Per-shipment (event-level), aggregated daily by Region
- **Source table**: `deliveries`
- **Drivers**: Logistics capacity, warehouse dispatch delay, courier performance, weather/disruption events

## 4. Dimensions (shared hierarchy across all KPIs)
- **Date** (day → week → month → quarter)
- **Region** (e.g. North, South, East, West)
- **Category** (e.g. Electronics, Apparel, Home)
- **Product** (SKU level, rolls up to Category)
- **Channel** (Online, Marketplace, In-store)
- **Customer Segment** (New, Returning, Premium)

## 5. Materiality Thresholds
| KPI | Statistical threshold | Business materiality threshold |
|---|---|---|
| Revenue | \|z-score\| > 2 vs 90-day rolling baseline | Absolute movement > ₹1,00,000/day at the affected grain |
| Orders | \|z-score\| > 2 | Movement > 50 orders/day at the affected grain |
| Inventory Availability | Drop > 10 percentage points | Affects a category contributing > 5% of regional revenue |
| Delivery SLA | Drop > 10 percentage points | Affects > 100 shipments/day |

Both thresholds must be crossed for a movement to enter the INVESTIGATE state (see
`02_ARCHITECTURE.md` §3.3) — this is the deterministic implementation of "statistical
significance AND business impact" from the Round 2 brief.

## 6. Lineage
Every KPI value shown anywhere in the system must be traceable to:
`source table → query/aggregation used → refresh timestamp`.
This lineage string is attached to every evidence item (see `01_PRD.md` §6.3) and displayed in
the UI's evidence panel.

## 7. Access Restrictions (row/column-level, tied to personas)
| Role | Row-level access | Column-level access |
|---|---|---|
| Business Leader (Regional) | Own region only | All KPI columns, no raw customer-level data |
| Analyst (Central) | All regions | All KPI columns + raw evidence detail |

This is the concrete implementation of the Round 2 "role-based security or entitlement
scenario" requirement — see `11_SECURITY_AND_API.md` for enforcement details.

## 8. Contract Enforcement
This contract is implemented in code as a config file (`kpi_contract.yaml` or equivalent) that
every downstream module (detection, decomposition, evidence, UI) reads from — no module
hardcodes a KPI formula or threshold independently. Example:
```yaml
kpi: Revenue
formula: "Orders * AOV"
dimensions: [Region, Category, Product, Channel, Customer Segment]
frequency: Daily
statistical_threshold: {zscore: 2, window_days: 90}
materiality_threshold: {absolute_inr: 100000}
source_table: orders
access:
  regional_leader: {row_filter: "region = :user_region", columns: "kpi_only"}
  analyst: {row_filter: "none", columns: "all"}
```