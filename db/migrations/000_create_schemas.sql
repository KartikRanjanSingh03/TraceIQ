-- db/migrations/000_create_schemas.sql
-- Stage 0 migration: create the three top-level schemas and grant access to app role.
-- Per 10_DATABASE_DESIGN.md §3:
--   raw      → source-aligned tables (orders, inventory_snapshots, deliveries, tickets_notes)
--   kpi      → semantic views (kpi_daily_revenue, kpi_daily_orders, etc.)
--   pipeline → system tables (kpi_alerts, hypotheses, recommendations, feedback_log, telemetry_log)
--
-- Prerequisites: traceiq_user role and traceiq database must already exist.
-- Run as superuser (postgres):
--   psql postgresql://postgres:<password>@127.0.0.1:5432/traceiq -f db/migrations/000_create_schemas.sql

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS kpi;
CREATE SCHEMA IF NOT EXISTS pipeline;

-- Grant the app role visibility and write access to all three schemas.
-- RLS policies (Stage 1+) restrict row-level access within these grants.
GRANT USAGE, CREATE ON SCHEMA raw      TO traceiq_user;
GRANT USAGE, CREATE ON SCHEMA kpi      TO traceiq_user;
GRANT USAGE, CREATE ON SCHEMA pipeline TO traceiq_user;
