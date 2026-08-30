"""
tests/test_stage1_data_and_contract.py — Stage 1 required tests (17_ROADMAP.md §2 Stage 1).

Tests:
  1. kpi_contract.yaml loads correctly and all KPIs are accessible
  2. Data generation runs without error (unit test — no DB needed)
  3. DB migrations apply cleanly (001-004)
  4. Data loads into raw tables
  5. KPI views return expected aggregates for a known date (analysis date)
  6. Sparse-history SKU present only from its launch date
  7. Planted evidence documents are present in tickets_notes

Run:
    pytest tests/test_stage1_data_and_contract.py -v
"""
from __future__ import annotations

import os
from datetime import date, timedelta

import pandas as pd
import pytest
from sqlalchemy import text

from db.connection import engine
from src.semantic.kpi_contract import CONTRACT

# ── Dates from generate_synthetic_data.py (mirrored here for assertions) ──
ANALYSIS_DATE   = date(2026, 8, 29)
OUTAGE_START    = ANALYSIS_DATE - timedelta(days=4)
SPARSE_LAUNCH   = ANALYSIS_DATE - timedelta(days=18)
SPARSE_SKU_ID   = "ELEC-MODELX-001"


# ═══════════════════════════════════════════════════════════════════════════
# 1. KPI CONTRACT TESTS
# ═══════════════════════════════════════════════════════════════════════════

class TestKPIContract:
    def test_contract_loads(self) -> None:
        """Contract singleton is populated."""
        assert CONTRACT is not None
        assert len(CONTRACT.kpis) == 5

    def test_all_kpi_names_present(self) -> None:
        names = CONTRACT.kpi_names
        for expected in ["Revenue", "Orders", "AOV", "InventoryAvailability", "DeliverySLA"]:
            assert expected in names, f"Missing KPI: {expected}"

    def test_revenue_formula(self) -> None:
        kpi = CONTRACT.get_kpi("Revenue")
        assert "SUM(revenue)" in kpi.formula
        assert kpi.unit == "INR"
        assert kpi.source_table == "raw.orders"

    def test_revenue_thresholds(self) -> None:
        kpi = CONTRACT.get_kpi("Revenue")
        assert kpi.statistical_threshold.zscore == 2
        assert kpi.statistical_threshold.window_days == 90
        assert kpi.materiality_threshold.absolute_inr == 100_000

    def test_inventory_formula(self) -> None:
        kpi = CONTRACT.get_kpi("InventoryAvailability")
        assert "stockout_flag" in kpi.formula.lower()
        assert kpi.source_table == "raw.inventory_snapshots"

    def test_access_rules(self) -> None:
        kpi = CONTRACT.get_kpi("Revenue")
        assert "regional_leader" in kpi.access
        assert "analyst" in kpi.access
        assert kpi.access["regional_leader"].row_filter == "region = :user_region"
        assert kpi.access["analyst"].row_filter == "none"

    def test_confidence_label_mapping(self) -> None:
        assert CONTRACT.confidence_label(0.80) == "Strong"
        assert CONTRACT.confidence_label(0.60) == "Moderate"
        assert CONTRACT.confidence_label(0.30) == "Weak"
        assert CONTRACT.confidence_label(0.10) == "Insufficient"

    def test_sparse_history_days(self) -> None:
        kpi = CONTRACT.get_kpi("Revenue")
        assert kpi.sparse_history_days == 30


# ═══════════════════════════════════════════════════════════════════════════
# 2. DATA GENERATION UNIT TESTS (no DB — fast)
# ═══════════════════════════════════════════════════════════════════════════

class TestDataGeneration:
    """Import and run generators, check shape/content without hitting DB."""

    @pytest.fixture(scope="class")
    def dataframes(self):
        from data.generate_synthetic_data import (
            generate_orders, generate_inventory_snapshots,
            generate_deliveries, generate_tickets_notes,
        )
        return {
            "orders":    generate_orders(),
            "inventory": generate_inventory_snapshots(),
            "deliveries": generate_deliveries(),
            "tickets":   generate_tickets_notes(),
        }

    def test_orders_schema(self, dataframes) -> None:
        df = dataframes["orders"]
        required = {"order_line_id", "order_id", "date", "region", "category",
                    "product_id", "channel", "customer_segment", "units",
                    "price", "discount_pct", "revenue", "product_launch_date"}
        assert required.issubset(df.columns), f"Missing columns: {required - set(df.columns)}"

    def test_orders_volume(self, dataframes) -> None:
        df = dataframes["orders"]
        # 120 days × 4 regions × 3 categories × ~base orders → expect at least 100k rows
        assert len(df) > 100_000, f"Expected >100k order rows, got {len(df):,}"

    def test_outage_injected(self, dataframes) -> None:
        """North Electronics orders collapse during outage window."""
        df = dataframes["orders"]
        # Use .map(lambda d: d.date()) to normalise object-dtype date column before comparison
        dates = df["date"].map(lambda d: d if isinstance(d, date) else d.date())
        outage_mask = (
            (df["region"] == "North") &
            (df["category"] == "Electronics") &
            (dates >= OUTAGE_START) &
            (dates <= ANALYSIS_DATE)
        )
        pre_mask = (
            (df["region"] == "North") &
            (df["category"] == "Electronics") &
            (dates >= OUTAGE_START - timedelta(days=5)) &
            (dates < OUTAGE_START)
        )
        outage_orders = len(df[outage_mask])
        pre_orders    = len(df[pre_mask])
        # During outage, orders should be dramatically lower (factor of ~3.5x drop)
        assert outage_orders < pre_orders * 0.40, (
            f"Outage not injected properly: outage={outage_orders}, pre={pre_orders}"
        )

    def test_sparse_sku_only_after_launch(self, dataframes) -> None:
        df = dataframes["orders"]
        dates = df["date"].map(lambda d: d if isinstance(d, date) else d.date())
        sparse_before = df[
            (df["product_id"] == SPARSE_SKU_ID) &
            (dates < SPARSE_LAUNCH)
        ]
        assert len(sparse_before) == 0, (
            f"Sparse SKU appears {len(sparse_before)} times before its launch date"
        )

    def test_sparse_sku_in_north_only(self, dataframes) -> None:
        df = dataframes["orders"]
        sparse_other = df[
            (df["product_id"] == SPARSE_SKU_ID) &
            (df["region"] != "North")
        ]
        assert len(sparse_other) == 0, (
            f"Sparse SKU appears in non-North regions: {sparse_other['region'].unique()}"
        )

    def test_inventory_outage_stockouts(self, dataframes) -> None:
        df = dataframes["inventory"]
        dates = df["date"].map(lambda d: d if isinstance(d, date) else d.date())
        outage_inv = df[
            (df["region"] == "North") &
            (df["category"] == "Electronics") &
            (dates >= OUTAGE_START) &
            (dates <= ANALYSIS_DATE)
        ]
        assert outage_inv["stockout_flag"].all(), (
            "Not all North/Electronics inventory rows are stockouts during outage"
        )

    def test_planted_warehouse_document(self, dataframes) -> None:
        df = dataframes["tickets"]
        planted = df[df["doc_id"] == "PLANTED-OUTAGE-001"]
        assert len(planted) == 1
        assert "WH-N01" in planted.iloc[0]["text"]
        assert planted.iloc[0]["region"] == "North"

    def test_competitor_signal_south_not_north(self, dataframes) -> None:
        """Competitor doc is tagged South — must NOT match North's primary anomaly segment."""
        df = dataframes["tickets"]
        comp = df[df["doc_id"] == "PLANTED-COMPETITOR-001"]
        assert len(comp) == 1
        assert comp.iloc[0]["region"] == "South"

    def test_revenue_non_negative(self, dataframes) -> None:
        df = dataframes["orders"]
        assert (df["revenue"] >= 0).all(), "Negative revenue values found"


# ═══════════════════════════════════════════════════════════════════════════
# 3. DATABASE INTEGRATION TESTS
# ═══════════════════════════════════════════════════════════════════════════

class TestDatabaseIntegration:
    """Requires DB with migrations applied and data loaded."""

    def test_raw_tables_exist(self) -> None:
        with engine.connect() as conn:
            result = conn.execute(text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'raw' ORDER BY table_name;"
            ))
            tables = {row[0] for row in result}
        expected = {"orders", "inventory_snapshots", "deliveries", "tickets_notes"}
        assert expected == tables, f"Raw tables mismatch: found {tables}"

    def test_pipeline_tables_exist(self) -> None:
        with engine.connect() as conn:
            result = conn.execute(text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'pipeline' ORDER BY table_name;"
            ))
            tables = {row[0] for row in result}
        expected = {"kpi_alerts", "hypotheses", "recommendations", "feedback_log", "telemetry_log"}
        assert expected == tables, f"Pipeline tables mismatch: found {tables}"

    def test_kpi_views_exist(self) -> None:
        with engine.connect() as conn:
            result = conn.execute(text(
                "SELECT table_name FROM information_schema.views "
                "WHERE table_schema = 'kpi' ORDER BY table_name;"
            ))
            views = {row[0] for row in result}
        expected = {
            "kpi_daily_revenue", "kpi_daily_inventory", "kpi_daily_delivery_sla",
            "revenue_regional_leader_view", "revenue_analyst_view",
        }
        assert expected.issubset(views), f"Missing KPI views: {expected - views}"

    def test_orders_loaded(self) -> None:
        with engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM raw.orders")).scalar()
        assert count > 100_000, f"Expected >100k order rows in DB, found {count:,}"

    def test_kpi_view_aggregates_analysis_date(self) -> None:
        """
        Core Stage 1 test: KPI view returns correct aggregates for the analysis date.
        On the analysis date (2026-08-29), North/Electronics revenue should be severely
        depressed vs South/Electronics (outage is active).
        """
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT region, SUM(revenue) AS total_revenue, SUM(order_count) AS total_orders
                FROM kpi.kpi_daily_revenue
                WHERE date = :d AND category = 'Electronics'
                GROUP BY region
                ORDER BY region;
            """), {"d": ANALYSIS_DATE})
            rows = {row[0]: {"revenue": float(row[1]), "orders": int(row[2])}
                    for row in result}

        assert "North" in rows, "North region missing from kpi_daily_revenue on analysis date"
        assert "South" in rows, "South region missing from kpi_daily_revenue on analysis date"

        north_rev = rows["North"]["revenue"]
        south_rev = rows["South"]["revenue"]

        # North Electronics revenue should be dramatically lower due to outage injection
        assert north_rev < south_rev * 0.40, (
            f"Expected North Electronics revenue to be <40% of South's on outage day. "
            f"North={north_rev:,.0f}, South={south_rev:,.0f}"
        )

    def test_sparse_sku_absent_before_launch_in_db(self) -> None:
        with engine.connect() as conn:
            count = conn.execute(text(
                "SELECT COUNT(*) FROM raw.orders "
                "WHERE product_id = :sku AND date < :launch"
            ), {"sku": SPARSE_SKU_ID, "launch": SPARSE_LAUNCH}).scalar()
        assert count == 0, (
            f"Sparse SKU appears {count} times before launch date in DB"
        )

    def test_planted_outage_doc_in_db(self) -> None:
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT doc_id, region FROM raw.tickets_notes "
                "WHERE doc_id = 'PLANTED-OUTAGE-001';"
            )).fetchone()
        assert row is not None, "Planted warehouse outage document not found in DB"
        assert row[1] == "North"

    def test_inventory_stockouts_during_outage(self) -> None:
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT COUNT(*) AS total, SUM(CASE WHEN stockout_flag THEN 1 ELSE 0 END) AS stockouts
                FROM raw.inventory_snapshots
                WHERE region = 'North' AND category = 'Electronics'
                  AND date BETWEEN :start AND :end;
            """), {"start": OUTAGE_START, "end": ANALYSIS_DATE})
            row = result.fetchone()
        total, stockouts = row[0], row[1]
        assert total > 0, "No inventory snapshots found for North/Electronics during outage"
        assert stockouts == total, (
            f"Expected all {total} North/Electronics snapshots to be stockouts during outage, "
            f"found only {stockouts}"
        )
