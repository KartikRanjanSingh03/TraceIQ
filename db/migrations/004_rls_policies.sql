-- db/migrations/004_rls_policies.sql
-- Stage 1: Row-Level Security policies per 10_DATABASE_DESIGN.md §5.
-- Enforcement point: RLS on raw.orders (and other raw tables).
-- Views use security_invoker (002_kpi_views.sql) so they inherit these policies.

-- Create the two DB roles for the two personas (11_SECURITY_AND_API.md §2)
DO $$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'regional_leader_role') THEN
        CREATE ROLE regional_leader_role;
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'analyst_role') THEN
        CREATE ROLE analyst_role;
    END IF;
END $$;

-- Grant these roles to the app user so the session can SET ROLE
GRANT regional_leader_role TO traceiq_user;
GRANT analyst_role TO traceiq_user;

-- ── raw.orders RLS ────────────────────────────────────────────────────────
ALTER TABLE raw.orders ENABLE ROW LEVEL SECURITY;

-- Regional Leader: own region only (region set per-session by FastAPI deps.py)
CREATE POLICY regional_leader_orders
    ON raw.orders
    FOR SELECT
    TO regional_leader_role
    USING (region = current_setting('app.current_user_region', true));

-- Analyst: all regions
CREATE POLICY analyst_orders
    ON raw.orders
    FOR SELECT
    TO analyst_role
    USING (true);

-- App user itself (INSERT/UPDATE for data loading — bypasses RLS via superuser COPY or explicit bypass)
-- We use FORCE ROW LEVEL SECURITY = false for the owner/superuser insert path intentionally.
-- For SELECT by the app user (traceiq_user) not acting as a role, grant all access (data loading only):
CREATE POLICY traceiq_user_orders_all
    ON raw.orders
    FOR ALL
    TO traceiq_user
    USING (true)
    WITH CHECK (true);

-- ── raw.inventory_snapshots RLS ───────────────────────────────────────────
ALTER TABLE raw.inventory_snapshots ENABLE ROW LEVEL SECURITY;

CREATE POLICY regional_leader_inventory
    ON raw.inventory_snapshots FOR SELECT TO regional_leader_role
    USING (region = current_setting('app.current_user_region', true));

CREATE POLICY analyst_inventory
    ON raw.inventory_snapshots FOR SELECT TO analyst_role
    USING (true);

CREATE POLICY traceiq_user_inventory_all
    ON raw.inventory_snapshots FOR ALL TO traceiq_user
    USING (true) WITH CHECK (true);

-- ── raw.deliveries RLS ────────────────────────────────────────────────────
ALTER TABLE raw.deliveries ENABLE ROW LEVEL SECURITY;

CREATE POLICY regional_leader_deliveries
    ON raw.deliveries FOR SELECT TO regional_leader_role
    USING (region = current_setting('app.current_user_region', true));

CREATE POLICY analyst_deliveries
    ON raw.deliveries FOR SELECT TO analyst_role
    USING (true);

CREATE POLICY traceiq_user_deliveries_all
    ON raw.deliveries FOR ALL TO traceiq_user
    USING (true) WITH CHECK (true);

-- tickets_notes: not region-restricted at row level (nullable region, queried via RAG)
-- No RLS on tickets_notes in MVP — per 11_SECURITY_AND_API.md §6 (no PII, semantic retrieval only)
