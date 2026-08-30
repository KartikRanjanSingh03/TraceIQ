"""
tests/test_stage5_structured_evidence.py — Stage 5 required tests (17_ROADMAP.md §2 Stage 5).

Required per 15_TESTING_STRATEGY.md:
  §4.5 Evidence Test — returns correct stockout/availability figures for the affected segment:
    - R01 evidence: stockout_rate ~100% on analysis_date for North/Electronics
    - R01 evidence: avg_stock_on_hand ~0 for North/Electronics
    - Control region (South/Electronics) stockout rate is low → supports region-specific diagnosis
    - R02 evidence: SLA breach rate elevated (but not extreme) for North region
    - R03 evidence: discount unchanged → NO supporting items
    - R04 evidence: competitor ticket count > 0 (planted doc exists)
    - retrieve_all_fired returns evidence for all fired hypotheses only

Run:
    pytest tests/test_stage5_structured_evidence.py -v
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.detection.change_detection import run_detection
from src.decomposition.kpi_decomposition import run_decomposition
from src.hypothesis.rules import generate_hypotheses
from src.evidence.structured_evidence import (
    EvidenceItem,
    StructuredEvidence,
    retrieve_evidence,
    retrieve_all_fired,
)

ANALYSIS_DATE = date(2026, 8, 29)
OUTAGE_GRAIN  = {"region": "North", "category": "Electronics"}


# ── Pipeline fixture ───────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def hypotheses():
    alert  = run_detection("Revenue", OUTAGE_GRAIN, ANALYSIS_DATE)
    assert alert.status == "INVESTIGATE"
    decomp = run_decomposition(alert)
    return generate_hypotheses(decomp)


@pytest.fixture(scope="module")
def r01_evidence(hypotheses):
    r01 = next(h for h in hypotheses if h.rule_id == "R01_INVENTORY_SHORTAGE")
    return retrieve_evidence(r01, OUTAGE_GRAIN, ANALYSIS_DATE)


@pytest.fixture(scope="module")
def r02_evidence(hypotheses):
    r02 = next(h for h in hypotheses if h.rule_id == "R02_DELIVERY_DISRUPTION")
    return retrieve_evidence(r02, OUTAGE_GRAIN, ANALYSIS_DATE)


@pytest.fixture(scope="module")
def r03_evidence(hypotheses):
    r03 = next(h for h in hypotheses if h.rule_id == "R03_PRICING_EFFECT")
    return retrieve_evidence(r03, OUTAGE_GRAIN, ANALYSIS_DATE)


@pytest.fixture(scope="module")
def r04_evidence(hypotheses):
    r04 = next(h for h in hypotheses if h.rule_id == "R04_COMPETITOR_ACTIVITY")
    return retrieve_evidence(r04, OUTAGE_GRAIN, ANALYSIS_DATE)


# ═══════════════════════════════════════════════════════════════════════════
# STRUCTURE
# ═══════════════════════════════════════════════════════════════════════════

class TestEvidenceStructure:
    def test_r01_returns_structured_evidence(self, r01_evidence) -> None:
        assert isinstance(r01_evidence, StructuredEvidence)

    def test_r01_correct_rule_id(self, r01_evidence) -> None:
        assert r01_evidence.hypothesis_rule_id == "R01_INVENTORY_SHORTAGE"

    def test_r01_has_items(self, r01_evidence) -> None:
        assert r01_evidence.has_evidence
        assert len(r01_evidence.items) >= 2

    def test_all_items_are_evidence_items(self, r01_evidence) -> None:
        for item in r01_evidence.items:
            assert isinstance(item, EvidenceItem)

    def test_items_have_descriptions(self, r01_evidence) -> None:
        for item in r01_evidence.items:
            assert item.description, f"EvidenceItem {item.metric_name} has empty description"

    def test_items_have_correct_source_table(self, r01_evidence) -> None:
        inv_items = [i for i in r01_evidence.items
                     if i.metric_name in ("stockout_rate", "avg_stock_on_hand")]
        for item in inv_items:
            assert item.source_table == "raw.inventory_snapshots"


# ═══════════════════════════════════════════════════════════════════════════
# §4.5 CORE ASSERTION: CORRECT FIGURES FOR INVENTORY SCENARIO
# ═══════════════════════════════════════════════════════════════════════════

class TestR01InventoryFigures:
    """
    15_TESTING_STRATEGY.md §4.5: stockout/availability figures must correctly
    reflect the injected outage (100% stockout, 0 stock on hand).
    """

    def test_stockout_rate_near_100pct(self, r01_evidence) -> None:
        """§4.5 core: North/Electronics stockout rate must be ~100% on analysis_date."""
        so_item = next(
            i for i in r01_evidence.items if i.metric_name == "stockout_rate"
        )
        assert so_item.metric_value >= 0.95, (
            f"Expected stockout_rate ~100% during outage, got {so_item.metric_value:.1%}"
        )

    def test_stockout_baseline_was_low(self, r01_evidence) -> None:
        """Baseline stockout rate (pre-outage) must be near 0% confirming this is an anomaly."""
        so_item = next(i for i in r01_evidence.items if i.metric_name == "stockout_rate")
        assert so_item.baseline_value < 0.05, (
            f"Baseline stockout rate should be <5%, got {so_item.baseline_value:.1%}. "
            "Data generation may have a problem."
        )

    def test_stockout_delta_is_large_positive(self, r01_evidence) -> None:
        """Delta must be a large positive value (baseline ~0 → actual ~1)."""
        so_item = next(i for i in r01_evidence.items if i.metric_name == "stockout_rate")
        assert so_item.delta > 0.80, (
            f"Stockout delta should be >0.80 (>80pp rise), got {so_item.delta:.2f}"
        )

    def test_stockout_item_supports_hypothesis(self, r01_evidence) -> None:
        so_item = next(i for i in r01_evidence.items if i.metric_name == "stockout_rate")
        assert so_item.supports_hypothesis is True

    def test_avg_stock_near_zero(self, r01_evidence) -> None:
        """Stock on hand should be 0 during the outage."""
        stk_item = next(
            i for i in r01_evidence.items if i.metric_name == "avg_stock_on_hand"
        )
        assert stk_item.metric_value < 10, (
            f"Avg stock on hand should be ~0 during outage, got {stk_item.metric_value:.1f}"
        )

    def test_control_region_stockout_is_low(self, r01_evidence) -> None:
        """
        The control-region stockout rate (South/Electronics) should be low,
        confirming the issue is region-specific, not category-wide.
        """
        ctrl_item = next(
            (i for i in r01_evidence.items if "control" in i.metric_name), None
        )
        assert ctrl_item is not None, "Control-region evidence item missing"
        assert ctrl_item.metric_value < 0.10, (
            f"Control region stockout should be <10%, got {ctrl_item.metric_value:.1%}"
        )
        assert ctrl_item.supports_hypothesis is True, (
            "Control region being clean SUPPORTS the hypothesis (region-specific issue)"
        )

    def test_majority_items_support_hypothesis(self, r01_evidence) -> None:
        """More supporting than contradicting items for the primary hypothesis."""
        assert len(r01_evidence.supporting) >= len(r01_evidence.contradicting), (
            f"Expected ≥ supporting items; "
            f"got {len(r01_evidence.supporting)} supporting, "
            f"{len(r01_evidence.contradicting)} contradicting"
        )


# ═══════════════════════════════════════════════════════════════════════════
# R02 DELIVERY DISRUPTION EVIDENCE
# ═══════════════════════════════════════════════════════════════════════════

class TestR02DeliveryEvidence:
    def test_r02_returns_evidence(self, r02_evidence) -> None:
        assert isinstance(r02_evidence, StructuredEvidence)
        assert r02_evidence.has_evidence

    def test_r02_breach_rate_item_present(self, r02_evidence) -> None:
        breach_item = next(
            (i for i in r02_evidence.items if i.metric_name == "sla_breach_rate"), None
        )
        assert breach_item is not None, "sla_breach_rate item missing from R02 evidence"

    def test_r02_breach_rate_in_range(self, r02_evidence) -> None:
        """SLA breach rate should be slightly elevated during outage but not extreme."""
        breach_item = next(i for i in r02_evidence.items if i.metric_name == "sla_breach_rate")
        # Injected: 18% breach rate during outage vs ~8% baseline
        assert 0.0 <= breach_item.metric_value <= 1.0
        assert breach_item.delta > 0, "Breach rate delta should be positive (elevated) during outage"


# ═══════════════════════════════════════════════════════════════════════════
# R03 PRICING EVIDENCE (no pricing anomaly injected)
# ═══════════════════════════════════════════════════════════════════════════

class TestR03PricingEvidence:
    def test_r03_has_items(self, r03_evidence) -> None:
        assert r03_evidence.has_evidence

    def test_r03_discount_item_present(self, r03_evidence) -> None:
        disc_item = next(
            (i for i in r03_evidence.items if i.metric_name == "avg_discount_pct"), None
        )
        assert disc_item is not None

    def test_r03_discount_not_supporting(self, r03_evidence) -> None:
        """
        No pricing change was injected — avg_discount_pct item should NOT
        support the pricing hypothesis (discount did not fall materially).
        """
        disc_item = next(i for i in r03_evidence.items if i.metric_name == "avg_discount_pct")
        # Discount delta should be small (noise-level, not >3pp)
        assert abs(disc_item.delta) < 0.05, (
            f"No pricing change injected; discount delta should be <5pp, "
            f"got {disc_item.delta * 100:.2f}pp"
        )
        assert disc_item.supports_hypothesis is False, (
            "Pricing evidence should NOT support hypothesis (no discount change injected)"
        )


# ═══════════════════════════════════════════════════════════════════════════
# R04 COMPETITOR EVIDENCE (planted ticket)
# ═══════════════════════════════════════════════════════════════════════════

class TestR04CompetitorEvidence:
    def test_r04_returns_evidence(self, r04_evidence) -> None:
        assert isinstance(r04_evidence, StructuredEvidence)

    def test_r04_competitor_ticket_found(self, r04_evidence) -> None:
        """Planted competitor market note must appear in the structured evidence."""
        ticket_item = next(
            (i for i in r04_evidence.items if i.metric_name == "competitor_ticket_count"), None
        )
        assert ticket_item is not None
        assert ticket_item.metric_value >= 1, (
            "Planted competitor ticket should be found in the last 14 days"
        )
        assert ticket_item.supports_hypothesis is True

    def test_r04_notes_mention_rag(self, r04_evidence) -> None:
        """Notes must flag that RAG validation is still required."""
        assert any("RAG" in note for note in r04_evidence.retrieval_notes), (
            "R04 evidence notes should flag that RAG (Stage 6) is still required"
        )


# ═══════════════════════════════════════════════════════════════════════════
# retrieve_all_fired — only returns evidence for fired hypotheses
# ═══════════════════════════════════════════════════════════════════════════

class TestRetrieveAllFired:
    @pytest.fixture(scope="class")
    def all_evidence(self, hypotheses):
        return retrieve_all_fired(hypotheses, OUTAGE_GRAIN, ANALYSIS_DATE)

    def test_returns_list_of_structured_evidence(self, all_evidence) -> None:
        assert isinstance(all_evidence, list)
        for ev in all_evidence:
            assert isinstance(ev, StructuredEvidence)

    def test_only_fired_hypotheses_included(self, hypotheses, all_evidence) -> None:
        fired_rule_ids = {h.rule_id for h in hypotheses if h.fired}
        returned_ids   = {ev.hypothesis_rule_id for ev in all_evidence}
        assert returned_ids == fired_rule_ids, (
            f"retrieve_all_fired returned evidence for unexpected rules: "
            f"expected {fired_rule_ids}, got {returned_ids}"
        )

    def test_r01_in_all_evidence(self, all_evidence) -> None:
        ids = {ev.hypothesis_rule_id for ev in all_evidence}
        assert "R01_INVENTORY_SHORTAGE" in ids
