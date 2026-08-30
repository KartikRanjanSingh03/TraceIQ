-- db/migrations/001_raw_tables.sql
-- Stage 1: Create raw source tables per 05_DATA_SCHEMA.md §2.
-- Run as superuser after 000_create_schemas.sql.

-- ── orders ────────────────────────────────────────────────────────────────
-- Grain: 1 row per order-line, daily batch.
-- order_id is NOT unique (same order spans multiple lines). PK is order_line_id.
CREATE TABLE IF NOT EXISTS raw.orders (
    order_line_id        TEXT        PRIMARY KEY,
    order_id             TEXT        NOT NULL,    -- logical order grouping (not PK)
    date                 DATE        NOT NULL,
    region               TEXT        NOT NULL,    -- North/South/East/West
    category             TEXT        NOT NULL,    -- Electronics/Apparel/Home
    product_id           TEXT        NOT NULL,
    channel              TEXT        NOT NULL,    -- Online/Marketplace/In-store
    customer_segment     TEXT        NOT NULL,    -- New/Returning/Premium
    units                INT         NOT NULL,
    price                NUMERIC     NOT NULL,
    discount_pct         NUMERIC     NOT NULL DEFAULT 0,
    revenue              NUMERIC     NOT NULL,    -- units * price * (1 - discount_pct)
    product_launch_date  DATE        NOT NULL     -- for sparse-history detection (05_DATA_SCHEMA.md §5)
);

CREATE INDEX IF NOT EXISTS idx_orders_date_region_cat
    ON raw.orders (date, region, category, product_id);
CREATE INDEX IF NOT EXISTS idx_orders_order_id
    ON raw.orders (order_id);                     -- join to deliveries

-- ── inventory_snapshots ───────────────────────────────────────────────────
-- Grain: 1 row per SKU-warehouse-day. Missing snapshots forward-filled in ETL layer.
CREATE TABLE IF NOT EXISTS raw.inventory_snapshots (
    snapshot_id                  TEXT        PRIMARY KEY,
    date                         DATE        NOT NULL,
    region                       TEXT        NOT NULL,
    category                     TEXT        NOT NULL,
    product_id                   TEXT        NOT NULL,
    warehouse_id                 TEXT        NOT NULL,
    stock_on_hand                INT         NOT NULL,
    stockout_flag                BOOLEAN     NOT NULL,
    replenishment_lead_time_days INT         NOT NULL DEFAULT 3,
    data_quality_flag            TEXT        DEFAULT NULL  -- 'forward_filled' if missing snapshot used
);

CREATE INDEX IF NOT EXISTS idx_inv_date_region_cat
    ON raw.inventory_snapshots (date, region, category, product_id);

-- ── deliveries ────────────────────────────────────────────────────────────
-- Grain: 1 row per shipment, event-level, hourly ingest.
CREATE TABLE IF NOT EXISTS raw.deliveries (
    delivery_id             TEXT        PRIMARY KEY,
    order_id                TEXT        NOT NULL,   -- logical FK to orders.order_id
    dispatch_timestamp      TIMESTAMP   NOT NULL,
    delivered_timestamp     TIMESTAMP   DEFAULT NULL,  -- NULL = in-transit
    region                  TEXT        NOT NULL,
    promised_sla_hours      INT         NOT NULL,
    actual_delivery_hours   NUMERIC     DEFAULT NULL,
    sla_breach_flag         BOOLEAN     NOT NULL DEFAULT FALSE,
    data_quality_flag       TEXT        DEFAULT NULL   -- 'in_transit', 'partial_day'
);

CREATE INDEX IF NOT EXISTS idx_del_dispatch_region
    ON raw.deliveries (dispatch_timestamp, region);

-- ── tickets_notes ─────────────────────────────────────────────────────────
-- Grain: 1 row per ticket/note, event-level, irregular.
-- text is embedded for RAG retrieval (Stage 6). Region/category/product_id nullable.
CREATE TABLE IF NOT EXISTS raw.tickets_notes (
    doc_id      TEXT        PRIMARY KEY,
    timestamp   TIMESTAMP   NOT NULL,
    region      TEXT        DEFAULT NULL,
    category    TEXT        DEFAULT NULL,
    product_id  TEXT        DEFAULT NULL,
    source      TEXT        NOT NULL,   -- support_ticket / ops_report / market_note
    text        TEXT        NOT NULL,
    embedding   TEXT        DEFAULT NULL  -- JSON-serialised vector, populated in Stage 6
);

CREATE INDEX IF NOT EXISTS idx_tickets_timestamp
    ON raw.tickets_notes (timestamp);

-- Grant SELECT/INSERT/UPDATE to app role for all raw tables
GRANT SELECT, INSERT, UPDATE ON raw.orders            TO traceiq_user;
GRANT SELECT, INSERT, UPDATE ON raw.inventory_snapshots TO traceiq_user;
GRANT SELECT, INSERT, UPDATE ON raw.deliveries        TO traceiq_user;
GRANT SELECT, INSERT, UPDATE ON raw.tickets_notes     TO traceiq_user;
