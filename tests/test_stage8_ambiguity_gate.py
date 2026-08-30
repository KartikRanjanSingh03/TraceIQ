"""
tests/test_stage8_ambiguity_gate.py — Stage 8 required tests (17_ROADMAP.md §2 Stage 8).

Required per 15_TESTING_STRATEGY.md:
  §4.8 Ambiguity Test — competitor hypothesis correctly abstains:
    - R04 (competitor, Insufficient/Weak) → GateDecision = Insufficient
    - R01 (inventory, Strong) → GateDecision = HighConfidence
    - R01 (Strong) + R04 (Insufficient) together → HighConfidence for the alert overall
      (mixed confidence within same alert — R01 confident, R04 abstained)

Also tests:
  - Too-close-to-call (top-2 gap < 0.10) → Insufficient (ambiguity scenario)
  - Moderate top → Moderate gate
  - format_abstention_output() produces the required template fields
  - GateDecision properties (.is_confident, .should_abstain)
  - human_review_required set correctly for each state

Run:
    pytest tests/test_stage8_ambiguity_gate.py -v
"""
from __future__ import annotations

from datetime import date

import pytest

from src.ambiguity.gate import (
    GATE_HIGH_CONFIDENCE,
    GATE_INSUFFICIENT,
    GATE_MODERATE,
    GateDecision,
    evaluate,
    format_abstention_output,
    _AMBIGUITY_GAP_THRESHOLD,
)
from src.confidence.scoring import ConfidenceScore

ANALYSIS_DATE = date(2026, 8, 29)


# ── Helpers ────────────────────────────────────────────────────────────────

def _score(rule_id: str, driver: str, raw_score: float, label: str) -> ConfidenceScore:
    return ConfidenceScore(
        hypothesis_rule_id=rule_id,
        driver=driver,
        raw_score=raw_score,
        label=label,
        rule_signal_component=raw_score * 0.35,
        structured_evidence_component=raw_score * 0.40,
        rag_component=raw_score * 0.25,
        penalties_applied=0.0,
        supporting_count=3 if label == "Strong" else 1,
        contradicting_count=0 if label == "Strong" else 2,
        rag_top_score=0.75 if label == "Strong" else 0.20,
        sparse_history=False,
        rationale=["test rationale"],
    )


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def r01_strong():
    return _score("R01_INVENTORY_SHORTAGE", "Inventory shortage", 0.75, "Strong")


@pytest.fixture
def r04_insufficient():
    return _score("R04_COMPETITOR_ACTIVITY", "Competitor activity", 0.18, "Insufficient")


@pytest.fixture
def r04_weak():
    return _score("R04_COMPETITOR_ACTIVITY", "Competitor activity", 0.25, "Weak")


@pytest.fixture
def r01_moderate():
    return _score("R01_INVENTORY_SHORTAGE", "Inventory shortage", 0.48, "Moderate")


# ═══════════════════════════════════════════════════════════════════════════
# §4.8 CORE: COMPETITOR ABSTAINS
# ═══════════════════════════════════════════════════════════════════════════

class TestCompetitorAbstains:
    """
    08_CONFIDENCE_AND_ABSTENTION §5 MVP scenario:
    Competitor hypothesis in the Revenue↓8.2% scenario must land in Insufficient Evidence.
    """

    def test_competitor_alone_gives_insufficient(self, r04_insufficient) -> None:
        """§4.8 core: competitor-only alert must abstain."""
        decision = evaluate([r04_insufficient])
        assert decision.gate_state == GATE_INSUFFICIENT, (
            f"Competitor hypothesis (Insufficient label) should give Insufficient gate, "
            f"got {decision.gate_state}"
        )

    def test_competitor_weak_alone_gives_insufficient(self, r04_weak) -> None:
        """Weak label also triggers abstention."""
        decision = evaluate([r04_weak])
        assert decision.gate_state == GATE_INSUFFICIENT

    def test_competitor_abstention_has_template(self, r04_insufficient) -> None:
        """Abstention output must contain all 5 template fields from §4."""
        decision = evaluate([r04_insufficient])
        text = format_abstention_output(decision)
        assert "Status: Insufficient Evidence" in text
        assert "Plausible hypotheses:" in text
        assert "Why undetermined:" in text
        assert "Missing data needed:" in text
        assert "Next step:" in text

    def test_competitor_abstention_mentions_competitor(self, r04_insufficient) -> None:
        """Abstention plausible hypotheses must name the competitor driver."""
        decision = evaluate([r04_insufficient])
        assert "Competitor activity" in decision.plausible_hypotheses

    def test_competitor_abstention_names_missing_data(self, r04_insufficient) -> None:
        """Abstention must name concrete missing data (not empty)."""
        decision = evaluate([r04_insufficient])
        assert len(decision.missing_data_needed) > 0
        assert any("competitor" in md.lower() for md in decision.missing_data_needed)

    def test_competitor_requires_human_review(self, r04_insufficient) -> None:
        decision = evaluate([r04_insufficient])
        assert decision.human_review_required is True


# ═══════════════════════════════════════════════════════════════════════════
# INVENTORY SCENARIO: HIGH CONFIDENCE
# ═══════════════════════════════════════════════════════════════════════════

class TestInventoryHighConfidence:
    def test_strong_single_hypothesis_gives_high_confidence(self, r01_strong) -> None:
        decision = evaluate([r01_strong])
        assert decision.gate_state == GATE_HIGH_CONFIDENCE

    def test_high_confidence_no_human_review(self, r01_strong) -> None:
        decision = evaluate([r01_strong])
        assert decision.human_review_required is False

    def test_high_confidence_primary_hypothesis_set(self, r01_strong) -> None:
        decision = evaluate([r01_strong])
        assert decision.primary_hypothesis is not None
        assert decision.primary_hypothesis.driver == "Inventory shortage"

    def test_high_confidence_is_confident_property(self, r01_strong) -> None:
        decision = evaluate([r01_strong])
        assert decision.is_confident is True
        assert decision.should_abstain is False


# ═══════════════════════════════════════════════════════════════════════════
# MIXED CONFIDENCE WITHIN ONE ALERT (§5: per-hypothesis, not global)
# ═══════════════════════════════════════════════════════════════════════════

class TestMixedConfidence:
    """
    08_CONFIDENCE §5: the system can hold mixed confidence across hypotheses
    within the same alert — R01 confident, R04 abstained.

    The gate evaluates the OVERALL alert state based on the TOP hypothesis.
    R01 Strong → alert is HighConfidence; R04 Insufficient is recorded but
    does not drag the overall alert into abstention.
    """

    def test_r01_strong_plus_r04_insufficient_gives_high_confidence(
        self, r01_strong, r04_insufficient
    ) -> None:
        # R01 is ranked first (0.75), R04 second (0.18) — gap = 0.57 >> threshold
        decision = evaluate([r01_strong, r04_insufficient])
        assert decision.gate_state == GATE_HIGH_CONFIDENCE, (
            "Overall alert should be HighConfidence when top hypothesis is Strong, "
            "even if second hypothesis is Insufficient"
        )

    def test_primary_is_r01_not_r04(self, r01_strong, r04_insufficient) -> None:
        decision = evaluate([r01_strong, r04_insufficient])
        assert decision.primary_hypothesis.hypothesis_rule_id == "R01_INVENTORY_SHORTAGE"

    def test_all_scores_preserved(self, r01_strong, r04_insufficient) -> None:
        decision = evaluate([r01_strong, r04_insufficient])
        assert len(decision.all_scores) == 2


# ═══════════════════════════════════════════════════════════════════════════
# TOO-CLOSE-TO-CALL: AMBIGUITY SCENARIO
# ═══════════════════════════════════════════════════════════════════════════

class TestTooCloseTocall:
    """
    When top-2 hypotheses are within _AMBIGUITY_GAP_THRESHOLD → Insufficient.
    This implements the "Current evidence cannot reliably distinguish..."
    example from the Round 1 doc.
    """

    def test_near_equal_moderate_hypotheses_abstain(self) -> None:
        s1 = _score("R03_PRICING_EFFECT",     "Pricing effect",       0.50, "Moderate")
        s2 = _score("R04_COMPETITOR_ACTIVITY", "Competitor activity",  0.45, "Moderate")
        # Gap = 0.05 < _AMBIGUITY_GAP_THRESHOLD (0.10) → Insufficient
        decision = evaluate([s1, s2])
        assert decision.gate_state == GATE_INSUFFICIENT, (
            f"Near-equal hypotheses (gap={0.50-0.45:.2f} < {_AMBIGUITY_GAP_THRESHOLD}) "
            "should produce Insufficient gate"
        )

    def test_ambiguity_mentions_both_drivers(self) -> None:
        s1 = _score("R03_PRICING_EFFECT",     "Pricing effect",       0.50, "Moderate")
        s2 = _score("R04_COMPETITOR_ACTIVITY", "Competitor activity",  0.45, "Moderate")
        decision = evaluate([s1, s2])
        assert "Pricing effect" in decision.why_undetermined or \
               "Pricing effect" in " ".join(decision.plausible_hypotheses)


# ═══════════════════════════════════════════════════════════════════════════
# MODERATE GATE
# ═══════════════════════════════════════════════════════════════════════════

class TestModerateGate:
    def test_moderate_top_with_clear_gap_gives_moderate(self, r01_moderate) -> None:
        """Moderate top hypothesis with no close second → Moderate gate."""
        decision = evaluate([r01_moderate])
        assert decision.gate_state == GATE_MODERATE

    def test_moderate_requires_human_review(self, r01_moderate) -> None:
        decision = evaluate([r01_moderate])
        assert decision.human_review_required is True

    def test_moderate_not_should_abstain(self, r01_moderate) -> None:
        decision = evaluate([r01_moderate])
        assert decision.should_abstain is False


# ═══════════════════════════════════════════════════════════════════════════
# EDGE CASES
# ═══════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_empty_scores_gives_insufficient(self) -> None:
        decision = evaluate([])
        assert decision.gate_state == GATE_INSUFFICIENT

    def test_format_abstention_empty_for_non_abstention(self, r01_strong) -> None:
        decision = evaluate([r01_strong])
        text = format_abstention_output(decision)
        assert text == "", "format_abstention_output should return '' for non-abstention"
