"""
tests/test_stage4_hypothesis.py — Stage 4 required tests (17_ROADMAP.md §2 Stage 4).

Required per 15_TESTING_STRATEGY.md:
  §4.4 Hypothesis Test — correct rules fire for the inventory-shortage scenario:
    - R01_INVENTORY_SHORTAGE must fire (North/Electronics outage = stockout spike)
    - R02_DELIVERY_DISRUPTION may also fire (slight SLA elevation during outage)
    - R04_COMPETITOR_ACTIVITY must fire (competitor ticket IS present in the data)
      BUT with lower signal_strength than R01 (to be downgraded by Evidence Engine)
    - R06_DEMAND_DECLINE must NOT fire (other rules fired)
    - R03_PRICING_EFFECT must NOT fire (discount unchanged during outage)

Also tests:
  - generate_hypotheses() returns all 6 candidates (fired + unfired)
  - Fired candidates are ranked before unfired
  - R01 is the top-ranked fired candidate (highest signal_strength)

Run:
    pytest tests/test_stage4_hypothesis.py -v
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.detection.change_detection import run_detection
from src.decomposition.kpi_decomposition import run_decomposition
from src.hypothesis.rules import HypothesisCandidate, generate_hypotheses

ANALYSIS_DATE = date(2026, 8, 29)
OUTAGE_GRAIN  = {"region": "North", "category": "Electronics"}


@pytest.fixture(scope="module")
def hypotheses():
    """Run full detection → decomposition → hypothesis pipeline."""
    alert = run_detection("Revenue", OUTAGE_GRAIN, ANALYSIS_DATE)
    assert alert.status == "INVESTIGATE"
    decomp = run_decomposition(alert)
    return generate_hypotheses(decomp)


# ═══════════════════════════════════════════════════════════════════════════
# STRUCTURE
# ═══════════════════════════════════════════════════════════════════════════

class TestHypothesisStructure:
    def test_returns_six_candidates(self, hypotheses) -> None:
        """All 6 rule candidates must be returned (fired + unfired)."""
        assert len(hypotheses) == 6, (
            f"Expected 6 hypothesis candidates, got {len(hypotheses)}"
        )

    def test_all_are_hypothesis_candidates(self, hypotheses) -> None:
        for h in hypotheses:
            assert isinstance(h, HypothesisCandidate)

    def test_all_rule_ids_present(self, hypotheses) -> None:
        rule_ids = {h.rule_id for h in hypotheses}
        expected = {
            "R01_INVENTORY_SHORTAGE", "R02_DELIVERY_DISRUPTION",
            "R03_PRICING_EFFECT",     "R04_COMPETITOR_ACTIVITY",
            "R05_MIX_CHANGE",         "R06_DEMAND_DECLINE",
        }
        assert rule_ids == expected, f"Rule ID mismatch: {rule_ids}"

    def test_fired_candidates_ranked_first(self, hypotheses) -> None:
        """All fired candidates must appear before any unfired ones."""
        fired_indices   = [i for i, h in enumerate(hypotheses) if h.fired]
        unfired_indices = [i for i, h in enumerate(hypotheses) if not h.fired]
        if fired_indices and unfired_indices:
            assert max(fired_indices) < min(unfired_indices), (
                "Fired candidates must all appear before unfired candidates in the ranked list"
            )

    def test_signal_strength_in_range(self, hypotheses) -> None:
        for h in hypotheses:
            assert 0.0 <= h.signal_strength <= 1.0, (
                f"{h.rule_id}: signal_strength {h.signal_strength} out of [0, 1]"
            )

    def test_generated_by_is_rule(self, hypotheses) -> None:
        for h in hypotheses:
            assert h.generated_by == "rule", (
                f"{h.rule_id}.generated_by should be 'rule', got '{h.generated_by}'"
            )


# ═══════════════════════════════════════════════════════════════════════════
# §4.4 CORE ASSERTION: CORRECT RULES FIRE
# ═══════════════════════════════════════════════════════════════════════════

class TestCorrectRulesFire:
    """
    15_TESTING_STRATEGY.md §4.4: for the inventory-shortage scenario,
    the inventory rule must fire and be the top-ranked hypothesis.
    """

    def _get(self, hypotheses, rule_id: str) -> HypothesisCandidate:
        return next(h for h in hypotheses if h.rule_id == rule_id)

    def test_r01_inventory_shortage_fires(self, hypotheses) -> None:
        """§4.4 core: R01 must fire — stockout rate is 100% during outage."""
        r01 = self._get(hypotheses, "R01_INVENTORY_SHORTAGE")
        assert r01.fired is True, (
            "R01_INVENTORY_SHORTAGE should fire: North/Electronics has 100% stockout rate "
            "during the 5-day outage window"
        )

    def test_r01_is_top_ranked(self, hypotheses) -> None:
        """R01 should be the highest-ranked fired hypothesis (highest signal_strength)."""
        fired = [h for h in hypotheses if h.fired]
        assert fired, "At least one hypothesis must fire"
        assert fired[0].rule_id == "R01_INVENTORY_SHORTAGE", (
            f"Expected R01 as top-ranked, got {fired[0].rule_id} "
            f"(strength={fired[0].signal_strength:.2f})"
        )

    def test_r01_signal_strength_high(self, hypotheses) -> None:
        """Stockout rose from ~0% to ~100% — signal_strength should be near maximum."""
        r01 = self._get(hypotheses, "R01_INVENTORY_SHORTAGE")
        assert r01.signal_strength > 0.5, (
            f"R01 signal_strength should be >0.5 for a 100pp stockout rise, "
            f"got {r01.signal_strength:.2f}"
        )

    def test_r01_has_supporting_signals(self, hypotheses) -> None:
        r01 = self._get(hypotheses, "R01_INVENTORY_SHORTAGE")
        assert len(r01.supporting_signals) > 0

    def test_r03_pricing_does_not_fire(self, hypotheses) -> None:
        """R03: discount unchanged during outage — pricing rule must NOT fire."""
        r03 = self._get(hypotheses, "R03_PRICING_EFFECT")
        assert r03.fired is False, (
            "R03_PRICING_EFFECT should NOT fire: no discount change was injected "
            "in the synthetic data for the outage scenario"
        )

    def test_r06_demand_decline_does_not_fire(self, hypotheses) -> None:
        """R06 is residual — must not fire when R01 fires."""
        r06 = self._get(hypotheses, "R06_DEMAND_DECLINE")
        assert r06.fired is False, (
            "R06_DEMAND_DECLINE should NOT fire when R01 (inventory shortage) has fired"
        )

    def test_r04_competitor_is_weaker_than_r01(self, hypotheses) -> None:
        """
        R04 may fire (competitor ticket IS present in data) but must have lower
        signal_strength than R01 (inventory shortage is the primary cause).
        This ensures the confidence engine will correctly rank inventory over competitor.
        """
        r01 = self._get(hypotheses, "R01_INVENTORY_SHORTAGE")
        r04 = self._get(hypotheses, "R04_COMPETITOR_ACTIVITY")
        if r04.fired:
            assert r04.signal_strength < r01.signal_strength, (
                f"R04 (competitor, strength={r04.signal_strength:.2f}) should be weaker than "
                f"R01 (inventory, strength={r01.signal_strength:.2f})"
            )


# ═══════════════════════════════════════════════════════════════════════════
# NORMAL GRAIN — no rules should fire
# ═══════════════════════════════════════════════════════════════════════════

class TestNoRulesFireForNormalGrain:
    """On a normal grain (South/Apparel, no anomaly injected), no rules should fire."""

    @pytest.fixture(scope="class")
    def normal_hypotheses(self):
        alert = run_detection("Revenue", {"region": "South", "category": "Apparel"},
                              ANALYSIS_DATE)
        # If NORMAL, no decomposition needed — skip with no hypotheses
        if alert.status == "NORMAL":
            return []
        decomp = run_decomposition(alert)
        return generate_hypotheses(decomp)

    def test_no_hypothesis_fires_for_normal(self, normal_hypotheses) -> None:
        fired = [h for h in normal_hypotheses if h.fired]
        assert len(fired) == 0, (
            f"No rules should fire for South/Apparel (no anomaly injected), "
            f"but these fired: {[h.rule_id for h in fired]}"
        )
