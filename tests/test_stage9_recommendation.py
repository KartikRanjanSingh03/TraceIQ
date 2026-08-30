"""
tests/test_stage9_recommendation.py — Stage 9 required tests (17_ROADMAP.md §2 Stage 9).

Required per 15_TESTING_STRATEGY.md:
  §4.9 Recommendation Relevance Test:
    - HighConfidence (R01 Strong) → Full recommendation mentioning supply chain action
    - Moderate (R01 Moderate)    → Conditional recommendation with condition_for_full
    - Insufficient (R04)         → Tier = "None", no action recommended
    - Recommendation is_actionable property correct for each tier

Also tests:
  - All required fields populated for Full and Conditional
  - Supply-chain action owner for R01
  - human_review_note present for Conditional, absent for Full

Run:
    pytest tests/test_stage9_recommendation.py -v
"""
from __future__ import annotations

from datetime import date

import pytest

from src.ambiguity.gate import (
    GateDecision,
    GATE_HIGH_CONFIDENCE,
    GATE_MODERATE,
    GATE_INSUFFICIENT,
)
from src.confidence.scoring import ConfidenceScore
from src.recommendation.recommendation_engine import Recommendation, generate_recommendation

ANALYSIS_DATE = date(2026, 8, 29)
OUTAGE_GRAIN  = {"region": "North", "category": "Electronics"}


# ── Helpers ────────────────────────────────────────────────────────────────

def _score(rule_id: str, driver: str, raw_score: float, label: str) -> ConfidenceScore:
    return ConfidenceScore(
        hypothesis_rule_id=rule_id, driver=driver,
        raw_score=raw_score, label=label,
        rule_signal_component=raw_score * 0.35,
        structured_evidence_component=raw_score * 0.40,
        rag_component=raw_score * 0.25,
        penalties_applied=0.0,
        supporting_count=3 if label == "Strong" else 1,
        contradicting_count=0,
        rag_top_score=0.75,
        sparse_history=False,
        rationale=["test"],
    )


def _gate(state: str, score: ConfidenceScore, why: str = "") -> GateDecision:
    return GateDecision(
        gate_state=state,
        primary_hypothesis=score,
        all_scores=[score],
        why_undetermined=why,
        plausible_hypotheses=[score.driver],
        human_review_required=(state != GATE_HIGH_CONFIDENCE),
    )


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def full_decision():
    s = _score("R01_INVENTORY_SHORTAGE", "Inventory shortage", 0.78, "Strong")
    return _gate(GATE_HIGH_CONFIDENCE, s)


@pytest.fixture
def conditional_decision():
    s = _score("R01_INVENTORY_SHORTAGE", "Inventory shortage", 0.50, "Moderate")
    return _gate(GATE_MODERATE, s)


@pytest.fixture
def none_decision():
    s = _score("R04_COMPETITOR_ACTIVITY", "Competitor activity", 0.18, "Insufficient")
    return _gate(GATE_INSUFFICIENT, s, why="No hypothesis reached Moderate confidence.")


# ═══════════════════════════════════════════════════════════════════════════
# §4.9 CORE: RECOMMENDATION RELEVANCE
# ═══════════════════════════════════════════════════════════════════════════

class TestRecommendationRelevance:
    def test_high_confidence_gives_full_tier(self, full_decision) -> None:
        rec = generate_recommendation(full_decision, ANALYSIS_DATE, OUTAGE_GRAIN)
        assert rec.tier == "Full", (
            f"HighConfidence gate must produce Full recommendation, got {rec.tier}"
        )

    def test_moderate_gives_conditional_tier(self, conditional_decision) -> None:
        rec = generate_recommendation(conditional_decision, ANALYSIS_DATE, OUTAGE_GRAIN)
        assert rec.tier == "Conditional", (
            f"Moderate gate must produce Conditional recommendation, got {rec.tier}"
        )

    def test_insufficient_gives_none_tier(self, none_decision) -> None:
        rec = generate_recommendation(none_decision, ANALYSIS_DATE, OUTAGE_GRAIN)
        assert rec.tier == "None", (
            f"Insufficient gate must produce None tier, got {rec.tier}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# STRUCTURE
# ═══════════════════════════════════════════════════════════════════════════

class TestRecommendationStructure:
    def test_returns_recommendation(self, full_decision) -> None:
        rec = generate_recommendation(full_decision)
        assert isinstance(rec, Recommendation)

    def test_full_has_all_required_fields(self, full_decision) -> None:
        rec = generate_recommendation(full_decision, ANALYSIS_DATE, OUTAGE_GRAIN)
        assert rec.driver == "Inventory shortage"
        assert rec.rule_id == "R01_INVENTORY_SHORTAGE"
        assert rec.action_owner
        assert rec.immediate_action
        assert rec.verification_step
        assert rec.escalation_path

    def test_conditional_has_condition_for_full(self, conditional_decision) -> None:
        rec = generate_recommendation(conditional_decision, ANALYSIS_DATE, OUTAGE_GRAIN)
        assert rec.condition_for_full, (
            "Conditional recommendation must have condition_for_full populated"
        )

    def test_full_has_no_condition(self, full_decision) -> None:
        rec = generate_recommendation(full_decision, ANALYSIS_DATE, OUTAGE_GRAIN)
        assert rec.condition_for_full == "", (
            "Full recommendation must NOT have condition_for_full"
        )

    def test_full_no_human_review_note(self, full_decision) -> None:
        rec = generate_recommendation(full_decision, ANALYSIS_DATE, OUTAGE_GRAIN)
        assert rec.human_review_note == ""

    def test_conditional_has_human_review_note(self, conditional_decision) -> None:
        rec = generate_recommendation(conditional_decision, ANALYSIS_DATE, OUTAGE_GRAIN)
        assert rec.human_review_note, "Conditional must have human_review_note"

    def test_none_has_no_actionable_content(self, none_decision) -> None:
        rec = generate_recommendation(none_decision, ANALYSIS_DATE, OUTAGE_GRAIN)
        assert "insufficient" in rec.immediate_action.lower() or \
               "no action" in rec.immediate_action.lower()


# ═══════════════════════════════════════════════════════════════════════════
# is_actionable PROPERTY
# ═══════════════════════════════════════════════════════════════════════════

class TestIsActionable:
    def test_full_is_actionable(self, full_decision) -> None:
        rec = generate_recommendation(full_decision)
        assert rec.is_actionable is True

    def test_conditional_is_actionable(self, conditional_decision) -> None:
        rec = generate_recommendation(conditional_decision)
        assert rec.is_actionable is True

    def test_none_is_not_actionable(self, none_decision) -> None:
        rec = generate_recommendation(none_decision)
        assert rec.is_actionable is False


# ═══════════════════════════════════════════════════════════════════════════
# SUPPLY CHAIN ACTION OWNER FOR R01
# ═══════════════════════════════════════════════════════════════════════════

class TestR01RecommendationContent:
    """
    §4.9 relevance: R01 recommendation must be supply-chain focused.
    """

    def test_r01_action_owner_is_supply_chain(self, full_decision) -> None:
        rec = generate_recommendation(full_decision, ANALYSIS_DATE, OUTAGE_GRAIN)
        assert "supply chain" in rec.action_owner.lower() or \
               "supply" in rec.action_owner.lower(), (
            f"R01 action_owner should mention supply chain, got '{rec.action_owner}'"
        )

    def test_r01_immediate_action_mentions_replenishment(self, full_decision) -> None:
        rec = generate_recommendation(full_decision, ANALYSIS_DATE, OUTAGE_GRAIN)
        text = rec.immediate_action.lower()
        assert "replenishment" in text or "restock" in text or "stock" in text, (
            f"R01 immediate action should mention replenishment, got: {rec.immediate_action}"
        )

    def test_r01_escalation_mentions_supply_chain(self, full_decision) -> None:
        rec = generate_recommendation(full_decision, ANALYSIS_DATE, OUTAGE_GRAIN)
        assert "supply chain" in rec.escalation_path.lower() or \
               "category" in rec.escalation_path.lower()

    def test_metadata_preserved(self, full_decision) -> None:
        rec = generate_recommendation(full_decision, ANALYSIS_DATE, OUTAGE_GRAIN)
        assert rec.analysis_date == ANALYSIS_DATE
        assert rec.grain_filters == OUTAGE_GRAIN
        assert rec.confidence_score == 0.78
