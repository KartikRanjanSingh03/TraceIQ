-- db/migrations/003_pipeline_tables.sql
-- Stage 1: pipeline system tables per 05_DATA_SCHEMA.md §3.
-- These store the output of each pipeline layer (alerts, hypotheses, recommendations, feedback, telemetry).

-- ── pipeline.kpi_alerts ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pipeline.kpi_alerts (
    alert_id             TEXT        PRIMARY KEY,
    kpi_name             TEXT        NOT NULL,
    date                 DATE        NOT NULL,
    grain_filters        JSONB       NOT NULL DEFAULT '{}',  -- e.g. {"region":"North","category":"Electronics"}
    actual_value         NUMERIC     NOT NULL,
    expected_value       NUMERIC     NOT NULL,
    zscore               NUMERIC     NOT NULL,
    persistence_days     INT         NOT NULL DEFAULT 1,
    business_impact_inr  NUMERIC     NOT NULL DEFAULT 0,
    status               TEXT        NOT NULL,               -- NORMAL / WATCH / INVESTIGATE
    created_at           TIMESTAMP   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_alerts_kpi_date_status
    ON pipeline.kpi_alerts (kpi_name, date, status);

-- ── pipeline.hypotheses ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pipeline.hypotheses (
    hypothesis_id        TEXT        PRIMARY KEY,
    alert_id             TEXT        NOT NULL REFERENCES pipeline.kpi_alerts(alert_id),
    driver               TEXT        NOT NULL,
    confidence_label     TEXT        NOT NULL,               -- Strong/Moderate/Weak/Insufficient
    supporting_evidence  JSONB       NOT NULL DEFAULT '[]',
    conflicting_evidence JSONB       NOT NULL DEFAULT '[]',
    generated_by         TEXT        NOT NULL DEFAULT 'rule', -- 'rule' | 'llm_suggested'
    created_at           TIMESTAMP   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_hyp_alert_id
    ON pipeline.hypotheses (alert_id);

-- ── pipeline.recommendations ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pipeline.recommendations (
    recommendation_id    TEXT        PRIMARY KEY,
    hypothesis_id        TEXT        NOT NULL REFERENCES pipeline.hypotheses(hypothesis_id),
    controllable_lever   TEXT        NOT NULL,
    action               TEXT        NOT NULL,
    expected_impact      TEXT        NOT NULL,
    owner_role           TEXT        NOT NULL,
    monitoring_kpi       TEXT        NOT NULL,
    created_at           TIMESTAMP   NOT NULL DEFAULT NOW()
);

-- ── pipeline.feedback_log ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pipeline.feedback_log (
    feedback_id          TEXT        PRIMARY KEY,
    recommendation_id    TEXT        NOT NULL REFERENCES pipeline.recommendations(recommendation_id),
    user_id              TEXT        NOT NULL,
    user_role            TEXT        NOT NULL,
    decision             TEXT        NOT NULL,   -- accept/reject/edit/request_more_evidence
    comment              TEXT        DEFAULT NULL,
    timestamp            TIMESTAMP   NOT NULL DEFAULT NOW(),
    actual_outcome       TEXT        DEFAULT NULL   -- filled in later if known
);

-- ── pipeline.telemetry_log ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pipeline.telemetry_log (
    call_id              TEXT        PRIMARY KEY,
    alert_id             TEXT        NOT NULL REFERENCES pipeline.kpi_alerts(alert_id),
    llm_provider         TEXT        NOT NULL,   -- 'claude' | 'gemini'
    latency_ms           INT         NOT NULL,
    input_tokens         INT         NOT NULL DEFAULT 0,
    output_tokens        INT         NOT NULL DEFAULT 0,
    estimated_cost_usd   NUMERIC     NOT NULL DEFAULT 0,
    timestamp            TIMESTAMP   NOT NULL DEFAULT NOW()
);

-- Grant full access to app role for all pipeline tables
GRANT SELECT, INSERT, UPDATE ON pipeline.kpi_alerts      TO traceiq_user;
GRANT SELECT, INSERT, UPDATE ON pipeline.hypotheses       TO traceiq_user;
GRANT SELECT, INSERT, UPDATE ON pipeline.recommendations  TO traceiq_user;
GRANT SELECT, INSERT, UPDATE ON pipeline.feedback_log     TO traceiq_user;
GRANT SELECT, INSERT, UPDATE ON pipeline.telemetry_log    TO traceiq_user;
