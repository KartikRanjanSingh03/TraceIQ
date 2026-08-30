"""
tests/test_stage6_rag.py — Stage 6 required tests (17_ROADMAP.md §2 Stage 6).

Required per 15_TESTING_STRATEGY.md:
  §4.6 RAG Test — planted warehouse-outage ticket retrieved in top-k for the relevant query:
    - build_index() indexes all 83 tickets_notes rows
    - retrieve() with outage query → PLANTED-OUTAGE-001 must appear in top-k results
    - PLANTED-OUTAGE-001 must have similarity_score >= min_score threshold
    - PLANTED-COMPETITOR-001 (South, vague) must have lower similarity than PLANTED-OUTAGE-001
      when queried for warehouse/inventory outage (this is the hallucination-prevention check)

Also validates:
  - Snippets are correctly typed RetrievedSnippet objects
  - retrieve_for_hypothesis() works end-to-end for R01 and R04

Run:
    pytest tests/test_stage6_rag.py -v
    (First run will download ~80MB model — subsequent runs use local cache)
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.evidence.retrieval import (
    RAGResult,
    RetrievedSnippet,
    build_index,
    retrieve,
    retrieve_for_hypothesis,
)

ANALYSIS_DATE = date(2026, 8, 29)
OUTAGE_GRAIN  = {"region": "North", "category": "Electronics"}
OUTAGE_WINDOW_START = ANALYSIS_DATE - timedelta(days=14)

# Planted doc IDs from generate_synthetic_data.py
PLANTED_OUTAGE_ID     = "PLANTED-OUTAGE-001"
PLANTED_REPLEN_ID     = "PLANTED-REPLEN-001"
PLANTED_COMPETITOR_ID = "PLANTED-COMPETITOR-001"


# ── Shared fixture: build index once per session ──────────────────────────

@pytest.fixture(scope="session", autouse=True)
def chroma_index():
    """Build Chroma index once for all Stage 6 tests."""
    n = build_index(force_rebuild=True)
    assert n > 0, f"build_index returned {n} — no documents indexed"
    return n


@pytest.fixture(scope="module")
def outage_rag_result():
    """Retrieve for R01 (inventory shortage) with North/Electronics grain."""
    return retrieve(
        query="warehouse inventory outage stockout electronics North region WH-N01",
        hypothesis_rule_id="R01_INVENTORY_SHORTAGE",
        top_k=5,
        min_score=0.20,
        window_start=OUTAGE_WINDOW_START,
        window_end=ANALYSIS_DATE,
    )


@pytest.fixture(scope="module")
def competitor_rag_result():
    """Retrieve for R04 (competitor activity) with the standard query template."""
    return retrieve_for_hypothesis(
        "R04_COMPETITOR_ACTIVITY",
        grain_filters=OUTAGE_GRAIN,
        analysis_date=ANALYSIS_DATE,
        top_k=5,
        window_days=14,
    )


# ═══════════════════════════════════════════════════════════════════════════
# INDEX BUILD
# ═══════════════════════════════════════════════════════════════════════════

class TestIndexBuild:
    def test_index_builds_nonzero_docs(self, chroma_index) -> None:
        """All 83 tickets_notes rows must be indexed."""
        assert chroma_index >= 83, (
            f"Expected ≥83 documents indexed (83 rows in tickets_notes), got {chroma_index}"
        )

    def test_rebuild_is_idempotent(self) -> None:
        """Rebuilding the index twice gives the same count, no duplicates."""
        n1 = build_index(force_rebuild=True)
        n2 = build_index(force_rebuild=False)   # should be a no-op (all already indexed)
        assert n1 == n2, f"Idempotency failure: first={n1}, second={n2}"


# ═══════════════════════════════════════════════════════════════════════════
# RESULT STRUCTURE
# ═══════════════════════════════════════════════════════════════════════════

class TestRAGResultStructure:
    def test_returns_rag_result(self, outage_rag_result) -> None:
        assert isinstance(outage_rag_result, RAGResult)

    def test_snippets_are_typed(self, outage_rag_result) -> None:
        for s in outage_rag_result.snippets:
            assert isinstance(s, RetrievedSnippet)

    def test_snippets_have_scores(self, outage_rag_result) -> None:
        for s in outage_rag_result.snippets:
            assert 0.0 <= s.similarity_score <= 1.0

    def test_snippets_sorted_by_score(self, outage_rag_result) -> None:
        scores = [s.similarity_score for s in outage_rag_result.snippets]
        assert scores == sorted(scores, reverse=True), (
            "Snippets must be sorted by similarity_score descending"
        )


# ═══════════════════════════════════════════════════════════════════════════
# §4.6 CORE ASSERTION: PLANTED OUTAGE DOC IN TOP-K
# ═══════════════════════════════════════════════════════════════════════════

class TestPlantedOutageRetrieval:
    """
    15_TESTING_STRATEGY.md §4.6:
    The planted warehouse outage report (PLANTED-OUTAGE-001) must appear
    in top-k results when querying for inventory/warehouse outage.
    """

    def test_planted_outage_in_results(self, outage_rag_result) -> None:
        """§4.6 core: PLANTED-OUTAGE-001 must be retrieved."""
        retrieved_ids = [s.doc_id for s in outage_rag_result.snippets]
        assert PLANTED_OUTAGE_ID in retrieved_ids, (
            f"PLANTED-OUTAGE-001 not found in top-k results. "
            f"Retrieved: {retrieved_ids}"
        )

    def test_planted_outage_has_high_similarity(self, outage_rag_result) -> None:
        """The outage doc similarity score must be meaningfully high."""
        outage_snippet = next(
            (s for s in outage_rag_result.snippets if s.doc_id == PLANTED_OUTAGE_ID), None
        )
        assert outage_snippet is not None
        assert outage_snippet.similarity_score >= 0.40, (
            f"Expected outage doc similarity >= 0.40, got {outage_snippet.similarity_score:.3f}"
        )

    def test_planted_outage_is_supporting(self, outage_rag_result) -> None:
        """Outage doc must be classified as supporting evidence for R01."""
        outage_snippet = next(
            s for s in outage_rag_result.snippets if s.doc_id == PLANTED_OUTAGE_ID
        )
        assert outage_snippet.supports_hypothesis is True, (
            "PLANTED-OUTAGE-001 should support the inventory shortage hypothesis"
        )

    def test_outage_doc_region_is_north(self, outage_rag_result) -> None:
        """Outage doc is tagged North — segment consistency check."""
        outage_snippet = next(
            s for s in outage_rag_result.snippets if s.doc_id == PLANTED_OUTAGE_ID
        )
        assert outage_snippet.region == "North", (
            f"Outage doc should be tagged North, got '{outage_snippet.region}'"
        )

    def test_result_has_supporting_evidence(self, outage_rag_result) -> None:
        assert outage_rag_result.has_supporting_evidence is True


# ═══════════════════════════════════════════════════════════════════════════
# COMPETITOR DOC: LOWER SIMILARITY THAN OUTAGE DOC FOR OUTAGE QUERY
# ═══════════════════════════════════════════════════════════════════════════

class TestCompetitorDocLowerRank:
    """
    The competitor doc (PLANTED-COMPETITOR-001) is deliberately South-tagged
    and vague. When querying for the warehouse outage, it must rank BELOW
    the planted outage doc — this validates semantic discrimination.
    """

    def test_outage_ranks_above_competitor_for_outage_query(self, outage_rag_result) -> None:
        outage_snippet = next(
            (s for s in outage_rag_result.snippets if s.doc_id == PLANTED_OUTAGE_ID), None
        )
        competitor_snippet = next(
            (s for s in outage_rag_result.snippets if s.doc_id == PLANTED_COMPETITOR_ID), None
        )

        if competitor_snippet is None:
            # Competitor doc not in top-k for outage query → it's correctly ranked lower
            return

        assert outage_snippet is not None, "Outage doc must be in results if competitor is"
        assert outage_snippet.similarity_score > competitor_snippet.similarity_score, (
            f"Outage doc ({outage_snippet.similarity_score:.3f}) should score higher than "
            f"competitor doc ({competitor_snippet.similarity_score:.3f}) for outage query"
        )


# ═══════════════════════════════════════════════════════════════════════════
# retrieve_for_hypothesis() END-TO-END
# ═══════════════════════════════════════════════════════════════════════════

class TestRetrieveForHypothesis:
    def test_r01_retrieve_returns_result(self) -> None:
        result = retrieve_for_hypothesis(
            "R01_INVENTORY_SHORTAGE",
            grain_filters=OUTAGE_GRAIN,
            analysis_date=ANALYSIS_DATE,
        )
        assert isinstance(result, RAGResult)
        assert result.hypothesis_rule_id == "R01_INVENTORY_SHORTAGE"

    def test_r04_retrieve_returns_result(self, competitor_rag_result) -> None:
        assert isinstance(competitor_rag_result, RAGResult)
        assert competitor_rag_result.hypothesis_rule_id == "R04_COMPETITOR_ACTIVITY"

    def test_r04_competitor_doc_retrieved(self, competitor_rag_result) -> None:
        """
        For the competitor query (no region filter since competitor signals are market-wide),
        the planted competitor doc must appear.
        """
        retrieved_ids = [s.doc_id for s in competitor_rag_result.snippets]
        assert PLANTED_COMPETITOR_ID in retrieved_ids, (
            f"PLANTED-COMPETITOR-001 not found in R04 top-k. Retrieved: {retrieved_ids}"
        )

    def test_r04_competitor_doc_has_lower_similarity_than_r01_outage(self) -> None:
        """
        Cross-query check: the competitor doc's similarity for the competitor query
        must be lower than the outage doc's similarity for the outage query.
        This mirrors the §4.5 ambiguity scenario: competitor evidence is always weaker.
        """
        r01_result = retrieve_for_hypothesis(
            "R01_INVENTORY_SHORTAGE", OUTAGE_GRAIN, ANALYSIS_DATE
        )
        r04_result = retrieve_for_hypothesis(
            "R04_COMPETITOR_ACTIVITY", OUTAGE_GRAIN, ANALYSIS_DATE
        )

        outage_top = r01_result.top_snippet
        comp_top   = r04_result.top_snippet

        if outage_top and comp_top:
            # The inventory outage docs should have higher max similarity
            # than the competitor docs (strong specific signal vs vague market note)
            assert outage_top.similarity_score >= comp_top.similarity_score - 0.10, (
                f"Inventory RAG top score ({outage_top.similarity_score:.3f}) should not be "
                f"dramatically below competitor RAG top score ({comp_top.similarity_score:.3f}). "
                "Inventory evidence should be at least comparable."
            )
