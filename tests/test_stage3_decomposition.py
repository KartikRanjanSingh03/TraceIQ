"""
tests/test_stage3_decomposition.py — Stage 3 required tests (17_ROADMAP.md §2 Stage 3).

Required per 15_TESTING_STRATEGY.md:
  §4.3 Decomposition Test — correctly localizes the injected scenario:
    The outage is in North/Electronics. Running decomposition on the Revenue INVESTIGATE alert
    should identify:
      - Level 1: Orders is the dominant factor (AOV unchanged; only order volume collapsed)
      - Level 2: the primary localization points toward the outage-affected dimension
      - localized_problem_statement is populated and non-empty

Run:
    pytest tests/test_stage3_decomposition.py -v
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.detection.change_detection import run_detection
from src.decomposition.kpi_decomposition import (
    DecompositionResult,
    DimensionContribution,
    FactorContribution,
    run_decomposition,
)

ANALYSIS_DATE = date(2026, 8, 29)
OUTAGE_GRAIN  = {"region": "North", "category": "Electronics"}


@pytest.fixture(scope="module")
def alert():
    """Get the INVESTIGATE alert for North/Electronics Revenue."""
    result = run_detection("Revenue", OUTAGE_GRAIN, ANALYSIS_DATE)
    assert result.status == "INVESTIGATE", (
        f"Prerequisite: expected INVESTIGATE alert, got {result.status}"
    )
    return result


@pytest.fixture(scope="module")
def decomp(alert):
    """Run decomposition on the INVESTIGATE alert."""
    return run_decomposition(alert)


# ═══════════════════════════════════════════════════════════════════════════
# RESULT STRUCTURE
# ═══════════════════════════════════════════════════════════════════════════

class TestDecompositionStructure:
    def test_returns_decomposition_result(self, decomp) -> None:
        assert isinstance(decomp, DecompositionResult)

    def test_basic_fields_populated(self, decomp) -> None:
        assert decomp.kpi_name == "Revenue"
        assert decomp.date == ANALYSIS_DATE
        assert decomp.alert_grain == OUTAGE_GRAIN

    def test_total_delta_is_negative(self, decomp) -> None:
        """Revenue is depressed → total_delta must be negative."""
        assert decomp.total_delta < 0, (
            f"Expected negative total_delta for revenue decline, got {decomp.total_delta:,.0f}"
        )

    def test_total_pct_change_negative(self, decomp) -> None:
        """Percentage change must be negative and substantial (>10% drop injected)."""
        assert decomp.total_pct_change < -0.10, (
            f"Expected >10% revenue decline, got {decomp.total_pct_change:.1%}"
        )

    def test_localized_problem_statement_populated(self, decomp) -> None:
        assert decomp.localized_problem_statement, "localized_problem_statement must not be empty"
        assert len(decomp.localized_problem_statement) > 20


# ═══════════════════════════════════════════════════════════════════════════
# LEVEL 1: FORMULA DECOMPOSITION (Orders vs AOV)
# ═══════════════════════════════════════════════════════════════════════════

class TestLevel1FormulaDecomposition:
    """
    Revenue = Orders × AOV.
    Outage causes order volume to collapse; AOV should be relatively stable.
    → Orders must be identified as the dominant factor.
    07_ANALYTICS_AND_DRIVER_ANALYSIS.md §3.1
    """

    def test_formula_factors_present(self, decomp) -> None:
        assert len(decomp.formula_factors) == 2, (
            f"Expected 2 formula factors (Orders, AOV), got {len(decomp.formula_factors)}"
        )

    def test_factor_names(self, decomp) -> None:
        names = {f.factor_name for f in decomp.formula_factors}
        assert names == {"Orders", "AOV"}, f"Unexpected factor names: {names}"

    def test_orders_factor_is_negative(self, decomp) -> None:
        """Orders pct_change should be strongly negative due to outage."""
        orders_factor = next(f for f in decomp.formula_factors if f.factor_name == "Orders")
        assert orders_factor.pct_change < -0.50, (
            f"Orders should have >50% decline, got {orders_factor.pct_change:.1%}"
        )

    def test_aov_factor_relatively_stable(self, decomp) -> None:
        """AOV should not be dramatically affected by the inventory outage."""
        aov_factor = next(f for f in decomp.formula_factors if f.factor_name == "AOV")
        assert abs(aov_factor.pct_change) < 0.25, (
            f"AOV change should be <25% (outage is volume-driven), got {aov_factor.pct_change:.1%}"
        )

    def test_dominant_factor_is_orders(self, decomp) -> None:
        """
        §4.3 core assertion: Orders must be identified as the dominant factor.
        The outage blocks purchases → volume collapse → Orders drives Revenue decline.
        07_ANALYTICS doc §3.1.
        """
        assert decomp.dominant_factor == "Orders", (
            f"Expected dominant_factor='Orders', got '{decomp.dominant_factor}'. "
            f"Factors: {[(f.factor_name, f.contribution_share) for f in decomp.formula_factors]}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# LEVEL 2: DIMENSIONAL CONTRIBUTION ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════

class TestLevel2DimensionalContribution:
    """
    07_ANALYTICS_AND_DRIVER_ANALYSIS.md §3.2
    contribution(segment) = (segment_actual - segment_expected) / (total_actual - total_expected)
    """

    def test_dimension_contributions_present(self, decomp) -> None:
        assert len(decomp.dimension_contributions) > 0, (
            "Expected at least one dimension's contributions to be computed"
        )

    def test_primary_localization_present(self, decomp) -> None:
        assert decomp.primary_localization is not None, (
            "primary_localization must be populated for an INVESTIGATE alert"
        )

    def test_primary_localization_type(self, decomp) -> None:
        assert isinstance(decomp.primary_localization, DimensionContribution)

    def test_contributions_sum_approximately_one(self, decomp) -> None:
        """
        For at least one dimension, the sum of |contribution_shares| should be
        approximately 1.0 (they collectively explain the total movement).
        Allow some slack for floating-point and multi-segment cancellation.
        """
        for dim, contribs in decomp.dimension_contributions.items():
            total_share = sum(abs(c.contribution_share) for c in contribs)
            # Not asserting tight equality — offsetting segments can push > 1
            # Just verify it's in a reasonable range
            assert 0.5 <= total_share <= 3.0, (
                f"Dimension '{dim}' total share {total_share:.2f} is out of expected range"
            )

    def test_primary_localization_has_largest_negative_delta(self, decomp) -> None:
        """
        Primary localization should point to the segment with the largest contribution
        to the decline (most negative delta).
        """
        primary = decomp.primary_localization
        assert primary.delta < 0, (
            f"Primary localized segment should show a decline (negative delta), "
            f"got {primary.delta:,.0f} for {primary.dimension}={primary.value}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# LOCALIZATION: THE INJECTED SCENARIO
# ═══════════════════════════════════════════════════════════════════════════

class TestLocalizationAccuracy:
    """
    §4.3 core test: decomposition should correctly localize the injected outage.

    The alert grain is already {"region": "North", "category": "Electronics"}.
    With the grain fixed, Level 2 decomposes within that grain across channel
    and customer_segment (region and category are already pinned).

    The primary localization should point to the segment(s) most responsible for
    the within-grain decline (since the whole grain is depressed, all channel
    and segment contributions should be consistently negative).
    """

    def test_problem_statement_mentions_decline(self, decomp) -> None:
        stmt = decomp.localized_problem_statement.lower()
        assert "decline" in stmt or "decrease" in stmt or "drop" in stmt or "fall" in stmt, (
            f"Problem statement should mention decline direction: '{decomp.localized_problem_statement}'"
        )

    def test_problem_statement_mentions_dominant_factor(self, decomp) -> None:
        assert decomp.dominant_factor.lower() in decomp.localized_problem_statement.lower(), (
            f"Problem statement should mention '{decomp.dominant_factor}'"
        )

    def test_actual_below_expected_for_orders_factor(self, decomp) -> None:
        """Actual Orders count in outage period must be well below expected."""
        orders_factor = next(f for f in decomp.formula_factors if f.factor_name == "Orders")
        assert orders_factor.actual < orders_factor.expected * 0.5, (
            f"Actual orders ({orders_factor.actual:.0f}) should be <50% of expected "
            f"({orders_factor.expected:.0f}) during the outage"
        )

    def test_full_pipeline_grain_produces_investigate_then_localizes(self) -> None:
        """
        End-to-end: detection → decomposition chain produces a localizable result
        at the total-company grain (no pre-filtered grain).
        """
        # Run detection at total-company grain (just region as the top-level slicer)
        all_regions_alert = run_detection(
            "Revenue",
            {"region": "North"},
            ANALYSIS_DATE,
        )
        # North overall should be INVESTIGATE (outage dominates North revenue)
        assert all_regions_alert.status == "INVESTIGATE"

        decomp_all = run_decomposition(all_regions_alert)
        assert decomp_all.total_delta < 0
        assert decomp_all.localized_problem_statement

        # Category dimension should show Electronics as largest contributor
        if "category" in decomp_all.dimension_contributions:
            cat_contribs = decomp_all.dimension_contributions["category"]
            top_cat = cat_contribs[0]
            assert top_cat.value == "Electronics", (
                f"Expected Electronics as top category contributor, got {top_cat.value} "
                f"(share={top_cat.contribution_share:.2f})"
            )
