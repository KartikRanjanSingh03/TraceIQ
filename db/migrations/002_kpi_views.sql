-- db/migrations/002_kpi_views.sql
-- Stage 1: KPI semantic views per 10_DATABASE_DESIGN.md §7 and 04_KPI_SEMANTIC_CONTRACT.md §3.
-- Views encode the KPI formulas — no application code computes a KPI formula independently.
-- Views use security_invoker so they respect RLS on the underlying raw tables (10_DATABASE_DESIGN.md §5).

-- ── kpi.kpi_daily_revenue ─────────────────────────────────────────────────
-- Implements: Revenue = SUM(revenue), Orders = COUNT(DISTINCT order_id), AOV = Revenue/Orders
-- Formula per 04_KPI_SEMANTIC_CONTRACT.md §3.1–3.3
CREATE OR REPLACE VIEW kpi.kpi_daily_revenue
WITH (security_invoker = true)
AS
SELECT
    date,
    region,
    category,
    product_id,
    channel,
    customer_segment,
    SUM(revenue)                                              AS revenue,
    COUNT(DISTINCT order_id)                                  AS order_count,
    SUM(revenue) / NULLIF(COUNT(DISTINCT order_id), 0)        AS aov
FROM raw.orders
GROUP BY date, region, category, product_id, channel, customer_segment;

-- ── kpi.kpi_daily_inventory ───────────────────────────────────────────────
-- Implements: Availability = 1 - (Stockout_SKU_Days / Total_SKU_Days)
-- Formula per 04_KPI_SEMANTIC_CONTRACT.md §3.4
CREATE OR REPLACE VIEW kpi.kpi_daily_inventory
WITH (security_invoker = true)
AS
SELECT
    date,
    region,
    category,
    product_id,
    COUNT(*)                                                                   AS total_sku_days,
    SUM(CASE WHEN stockout_flag THEN 1 ELSE 0 END)                            AS stockout_sku_days,
    1.0 - (
        SUM(CASE WHEN stockout_flag THEN 1 ELSE 0 END)::FLOAT
        / NULLIF(COUNT(*), 0)
    )                                                                          AS availability,
    BOOL_OR(data_quality_flag = 'forward_filled')                             AS has_forward_filled
FROM raw.inventory_snapshots
GROUP BY date, region, category, product_id;

-- ── kpi.kpi_daily_delivery_sla ────────────────────────────────────────────
-- Implements: SLA_Compliance = 1 - (SLA_Breaches / Total_Deliveries)
-- Grain: aggregated to dispatch-date level. in-transit excluded (data_quality_flag).
-- Formula per 04_KPI_SEMANTIC_CONTRACT.md §3.5
CREATE OR REPLACE VIEW kpi.kpi_daily_delivery_sla
WITH (security_invoker = true)
AS
SELECT
    dispatch_timestamp::DATE                                   AS date,
    region,
    COUNT(*)                                                   AS total_deliveries,
    SUM(CASE WHEN sla_breach_flag THEN 1 ELSE 0 END)          AS sla_breaches,
    1.0 - (
        SUM(CASE WHEN sla_breach_flag THEN 1 ELSE 0 END)::FLOAT
        / NULLIF(COUNT(*), 0)
    )                                                          AS sla_compliance
FROM raw.deliveries
WHERE data_quality_flag IS DISTINCT FROM 'in_transit'   -- exclude unsettled same-day records
GROUP BY dispatch_timestamp::DATE, region;


-- ── Role-specific column-level views (10_DATABASE_DESIGN.md §6) ──────────

-- Regional Leader: KPI columns only, no raw customer-level detail
CREATE OR REPLACE VIEW kpi.revenue_regional_leader_view
WITH (security_invoker = true)
AS
SELECT date, region, category, revenue, order_count, aov
FROM kpi.kpi_daily_revenue;

-- Analyst: full detail
CREATE OR REPLACE VIEW kpi.revenue_analyst_view
WITH (security_invoker = true)
AS
SELECT * FROM kpi.kpi_daily_revenue;

-- Grant SELECT to app role
GRANT SELECT ON kpi.kpi_daily_revenue             TO traceiq_user;
GRANT SELECT ON kpi.kpi_daily_inventory           TO traceiq_user;
GRANT SELECT ON kpi.kpi_daily_delivery_sla        TO traceiq_user;
GRANT SELECT ON kpi.revenue_regional_leader_view  TO traceiq_user;
GRANT SELECT ON kpi.revenue_analyst_view          TO traceiq_user;
