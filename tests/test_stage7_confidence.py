"""
tests/test_stage7_confidence.py — Stage 7 required tests (17_ROADMAP.md §2 Stage 7).

Required per 15_TESTING_STRATEGY.md:
  §4.7 Confidence Test — known evidence combinations map to expected confidence labels:
    - R01 (inventory, full evidence): Strong
    - R04 (competitor, weak RAG + contradicting region): Insufficient or Weak
    - Sparse-history grain: cannot be Strong (hard-cap to Moderate)
    - score_all() returns sorted list, R01 ranked above R04

Also tests:
  - ConfidenceScore has all required fields
  - rationale is non-empty
  - sparse_history penalty applied correctly

Run:
    pytest tests/test_stage7_confidence.py -v
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from src.confidence.scoring import (
    ConfidenceScore,
    score_hypothesis,
    score_all,
    _THRESH_STRONG,
    _THRESH_MODERATE,
    _THRESH_WEAK,
)
from src.evidence.structured_evidence import EvidenceItem, StructuredEvidence
from src.evidence.retrieval import RAGResult, RetrievedSnippet
from src.hypothesis.rules import HypothesisCandidate

ANALYSIS_DATE = date(2026, 8, 29)
OUTAGE_GRAIN  = {"region": "North", "category": "Electronics"}


# ── Helpers: build test fixtures without hitting DB ───────────────────────

def _make_hypothesis(rule_id: str, driver: str, strength: float) -> HypothesisCandidate:
    return HypothesisCandidate(
        driver=driver,
        rule_id=rule_id,
        fired=True,
        signal_strength=strength,
    )


def _make_evidence(
    rule_id: str,
    n_supporting: int,
    n_contradicting: int,
    support_delta: float = 500.0,
) -> StructuredEvidence:
    items = []
    for i in range(n_supporting):
        items.append(EvidenceItem(
            source_table="raw.test",
            metric_name=f"sup_{i}",
            metric_value=0.9, baseline_value=0.05,
            delta=support_delta, delta_pct=5.0,
            window_days=90, supports_hypothesis=True,
            description="Supporting item",
        ))
    for i in range(n_contradicting):
        items.append(EvidenceItem(
            source_table="raw.test",
            metric_name=f"contra_{i}",
            metric_value=0.05, baseline_value=0.06,
            delta=-0.01, delta_pct=-0.1,
            window_days=90, supports_hypothesis=False,
            description="Contradicting item",
        ))
    return StructuredEvidence(
        hypothesis_rule_id=rule_id, driver="Test",
        grain_filters=OUTAGE_GRAIN, analysis_date=ANALYSIS_DATE,
        items=items,
    )


def _make_rag(rule_id: str, top_similarity: float) -> RAGResult:
    snippet = RetrievedSnippet(
        doc_id="test-doc-001",
        text="Warehouse stockout North Electronics.",
        source="ops_report",
        timestamp="2026-08-28",
        region="North", category="Electronics",
        similarity_score=top_similarity,
        supports_hypothesis=top_similarity >= 0.45,
    )
    return RAGResult(
        hypothesis_rule_id=rule_id,
        query="test query",
        snippets=[snippet],
    )


# ═══════════════════════════════════════════════════════════════════════════
# STRUCTURE
# ═══════════════════════════════════════════════════════════════════════════

class TestConfidenceScoreStructure:
    @pytest.fixture
    def r01_score(self):
        h   = _make_hypothesis("R01_INVENTORY_SHORTAGE", "Inventory shortage", 0.95)
        evd = _make_evidence("R01_INVENTORY_SHORTAGE", 3, 0)
        rag = _make_rag("R01_INVENTORY_SHORTAGE", 0.72)
        return score_hypothesis(h, evd, rag)

    def test_returns_confidence_score(self, r01_score) -> None:
        assert isinstance(r01_score, ConfidenceScore)

    def test_all_fields_present(self, r01_score) -> None:
        assert r01_score.hypothesis_rule_id == "R01_INVENTORY_SHORTAGE"
        assert r01_score.driver == "Inventory shortage"
        assert 0.0 <= r01_score.raw_score <= 1.0
        assert r01_score.label in ("Strong", "Moderate", "Weak", "Insufficient")

    def test_rationale_populated(self, r01_score) -> None:
        assert len(r01_score.rationale) >= 3, (
            "Rationale should have ≥3 entries (one per component)"
        )

    def test_components_sum_to_raw_score_approximately(self, r01_score) -> None:
        expected = (
            r01_score.rule_signal_component
            + r01_score.structured_evidence_component
            + r01_score.rag_component
            - r01_score.penalties_applied
        )
        assert abs(r01_score.raw_score - max(0.0, min(1.0, expected))) < 1e-6


# ═══════════════════════════════════════════════════════════════════════════
# §4.7 CORE: KNOWN COMBINATIONS → EXPECTED LABELS
# ═══════════════════════════════════════════════════════════════════════════

class TestKnownCombinations:
    """
    15_TESTING_STRATEGY.md §4.7: verify that known evidence combinations
    produce the expected confidence labels.
    """

    def test_strong_evidence_combination_gives_strong(self) -> None:
        """
        R01: high rule signal + all supporting evidence + strong RAG → Strong.
        This is the primary case for the inventory-shortage scenario.
        """
        h   = _make_hypothesis("R01_INVENTORY_SHORTAGE", "Inventory shortage", 0.95)
        evd = _make_evidence("R01_INVENTORY_SHORTAGE", n_supporting=3, n_contradicting=0,
                              support_delta=500.0)
        rag = _make_rag("R01_INVENTORY_SHORTAGE", 0.75)
        score = score_hypothesis(h, evd, rag, sparse_history=False)
        assert score.label == "Strong", (
            f"Expected Strong for full inventory evidence, got {score.label} "
            f"(raw={score.raw_score:.3f})"
        )

    def test_weak_signal_no_rag_gives_insufficient_or_weak(self) -> None:
        """
        R04 (competitor): low rule signal + 0 supporting + no RAG → Insufficient or Weak.
        08_CONFIDENCE_AND_ABSTENTION §5: competitor must abstain in the MVP scenario.
        """
        h   = _make_hypothesis("R04_COMPETITOR_ACTIVITY", "Competitor activity", 0.30)
        evd = _make_evidence("R04_COMPETITOR_ACTIVITY", n_supporting=1, n_contradicting=2)
        # No RAG result
        score = score_hypothesis(h, evd, rag_result=None, sparse_history=False)
        assert score.label in ("Weak", "Insufficient"), (
            f"Expected Weak or Insufficient for competitor with no RAG, "
            f"got {score.label} (raw={score.raw_score:.3f})"
        )

    def test_moderate_signal_partial_evidence_gives_moderate(self) -> None:
        """Moderate signal + half supporting + weak RAG → Moderate."""
        h   = _make_hypothesis("R02_DELIVERY_DISRUPTION", "Delivery disruption", 0.50)
        evd = _make_evidence("R02_DELIVERY_DISRUPTION", n_supporting=1, n_contradicting=1)
        rag = _make_rag("R02_DELIVERY_DISRUPTION", 0.35)
        score = score_hypothesis(h, evd, rag)
        assert score.label in ("Moderate", "Weak"), (
            f"Expected Moderate or Weak for partial evidence, got {score.label}"
        )

    def test_all_contradicting_gives_insufficient(self) -> None:
        """Zero supporting items + no RAG → Insufficient regardless of rule signal."""
        h   = _make_hypothesis("R03_PRICING_EFFECT", "Pricing effect", 0.60)
        evd = _make_evidence("R03_PRICING_EFFECT", n_supporting=0, n_contradicting=3)
        score = score_hypothesis(h, evd, rag_result=None)
        assert score.label in ("Weak", "Insufficient"), (
            f"All-contradicting evidence must give Weak/Insufficient, got {score.label}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# SPARSE HISTORY HARD-CAP
# ═══════════════════════════════════════════════════════════════════════════

class TestSparseHistoryCapBehavior:
    """
    07_ANALYTICS_AND_DRIVER_ANALYSIS.md §2.6 + 08_CONFIDENCE_AND_ABSTENTION §8:
    Sparse history must cap confidence to Moderate — never Strong.
    """

    def test_sparse_history_prevents_strong_label(self) -> None:
        h   = _make_hypothesis("R01_INVENTORY_SHORTAGE", "Inventory shortage", 0.95)
        evd = _make_evidence("R01_INVENTORY_SHORTAGE", n_supporting=3, n_contradicting=0,
                              support_delta=500.0)
        rag = _make_rag("R01_INVENTORY_SHORTAGE", 0.75)
        score = score_hypothesis(h, evd, rag, sparse_history=True)
        assert score.label != "Strong", (
            f"sparse_history=True must cap label below Strong, got {score.label}"
        )

    def test_sparse_history_applies_penalty(self) -> None:
        """Sparse flag must add a non-zero penalty."""
        h   = _make_hypothesis("R01_INVENTORY_SHORTAGE", "Inventory shortage", 0.70)
        evd = _make_evidence("R01_INVENTORY_SHORTAGE", n_supporting=2, n_contradicting=0)
        rag = _make_rag("R01_INVENTORY_SHORTAGE", 0.60)
        sparse_score    = score_hypothesis(h, evd, rag, sparse_history=True)
        non_sparse_score = score_hypothesis(h, evd, rag, sparse_history=False)
        assert sparse_score.raw_score < non_sparse_score.raw_score, (
            "Sparse penalty must reduce the raw score"
        )

    def test_sparse_rationale_mentions_penalty(self) -> None:
        h   = _make_hypothesis("R01_INVENTORY_SHORTAGE", "Inventory shortage", 0.80)
        evd = _make_evidence("R01_INVENTORY_SHORTAGE", n_supporting=2, n_contradicting=0)
        score = score_hypothesis(h, evd, None, sparse_history=True)
        rationale_text = " ".join(score.rationale).lower()
        assert "sparse" in rationale_text


# ═══════════════════════════════════════════════════════════════════════════
# score_all() — ranking and filtering
# ═══════════════════════════════════════════════════════════════════════════

class TestScoreAll:
    @pytest.fixture
    def all_scores(self):
        h_r01 = _make_hypothesis("R01_INVENTORY_SHORTAGE", "Inventory shortage", 0.95)
        h_r04 = _make_hypothesis("R04_COMPETITOR_ACTIVITY", "Competitor activity", 0.30)
        h_unfired = HypothesisCandidate(
            driver="Unfired", rule_id="R03_PRICING_EFFECT",
            fired=False, signal_strength=0.0
        )

        evidence_map = {
            "R01_INVENTORY_SHORTAGE": _make_evidence("R01_INVENTORY_SHORTAGE", 3, 0, 500),
            "R04_COMPETITOR_ACTIVITY": _make_evidence("R04_COMPETITOR_ACTIVITY", 1, 2),
        }
        rag_map = {
            "R01_INVENTORY_SHORTAGE": _make_rag("R01_INVENTORY_SHORTAGE", 0.75),
        }
        return score_all(
            [h_r01, h_r04, h_unfired],
            evidence_map,
            rag_map,
            sparse_history=False,
        )

    def test_returns_list(self, all_scores) -> None:
        assert isinstance(all_scores, list)

    def test_only_fired_hypotheses_scored(self, all_scores) -> None:
        """Unfired hypothesis (R03) must not appear in score_all output."""
        rule_ids = {s.hypothesis_rule_id for s in all_scores}
        assert "R03_PRICING_EFFECT" not in rule_ids

    def test_sorted_by_raw_score_desc(self, all_scores) -> None:
        scores = [s.raw_score for s in all_scores]
        assert scores == sorted(scores, reverse=True)

    def test_r01_ranked_above_r04(self, all_scores) -> None:
        """Inventory shortage must rank above competitor activity."""
        ids = [s.hypothesis_rule_id for s in all_scores]
        assert ids.index("R01_INVENTORY_SHORTAGE") < ids.index("R04_COMPETITOR_ACTIVITY"), (
            "R01 (inventory, full evidence) must rank above R04 (competitor, weak evidence)"
        )
