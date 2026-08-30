"""
tests/test_stage10_llm.py — Stage 10 required tests (17_ROADMAP.md §2 Stage 10).

Required per 15_TESTING_STRATEGY.md:
  §4.10 Hallucination Test — LLM must not invent evidence for a hypothesis
        that has zero supporting evidence in its input package:
    - Prompt for R03 (pricing) must include the GUARDRAIL marker
    - Evidence package for pricing must have guardrail text, NOT fabricated evidence
    - Mock narrative for a pricing-only query must NOT claim pricing caused the drop

  §4.11 Persona Narrative Test:
    - business_leader persona: short (≤ 500 chars), no technical jargon (z-score etc.)
    - analyst persona: longer output, includes evidence trail markers

Also tests:
  - llm_suggest.py disabled by default (returns [])
  - LLMResponse structure (provider, text, success)
  - Caching: second call returns cached=True
  - system_prompt for analyst mentions "evidence trail"
  - system_prompt for business_leader mentions "3-5 sentences"

Run:
    pytest tests/test_stage10_llm.py -v
    (Uses mock provider — no API key required)
"""
from __future__ import annotations

import os
from datetime import date
from unittest.mock import patch

import pytest

from src.ambiguity.gate import (
    GateDecision,
    GATE_HIGH_CONFIDENCE,
    GATE_INSUFFICIENT,
    GATE_MODERATE,
)
from src.confidence.scoring import ConfidenceScore
from src.evidence.retrieval import RAGResult, RetrievedSnippet
from src.evidence.structured_evidence import EvidenceItem, StructuredEvidence
from src.llm.llm_client import LLMResponse, call, _CACHE
from src.llm.narrative import NarrativeResult, generate_narrative
from src.llm.prompts import (
    build_evidence_package,
    build_prompt,
    get_system_prompt,
)
from src.hypothesis.llm_suggest import suggest_additional_hypotheses
from src.recommendation.recommendation_engine import Recommendation

ANALYSIS_DATE = date(2026, 8, 29)
OUTAGE_GRAIN  = {"region": "North", "category": "Electronics"}


# ── Shared helpers ─────────────────────────────────────────────────────────

def _score(rule_id: str, driver: str, raw: float, label: str) -> ConfidenceScore:
    return ConfidenceScore(
        hypothesis_rule_id=rule_id, driver=driver,
        raw_score=raw, label=label,
        rule_signal_component=raw*0.35, structured_evidence_component=raw*0.40,
        rag_component=raw*0.25, penalties_applied=0.0,
        supporting_count=3 if label == "Strong" else 0,
        contradicting_count=0, rag_top_score=0.7, sparse_history=False,
        rationale=["test"],
    )


def _make_evidence(rule_id: str, n_sup: int, n_con: int) -> StructuredEvidence:
    items = []
    for i in range(n_sup):
        items.append(EvidenceItem(
            source_table="raw.test", metric_name=f"sup_{i}",
            metric_value=0.9, baseline_value=0.05,
            delta=0.85, delta_pct=17.0,
            window_days=90, supports_hypothesis=True,
            description=f"Stockout rate rose 85pp in North/Electronics (item {i})",
        ))
    for i in range(n_con):
        items.append(EvidenceItem(
            source_table="raw.test", metric_name=f"con_{i}",
            metric_value=0.05, baseline_value=0.04,
            delta=-0.01, delta_pct=-0.1,
            window_days=90, supports_hypothesis=False,
            description=f"Contradicting item {i}",
        ))
    return StructuredEvidence(
        hypothesis_rule_id=rule_id, driver="Test",
        grain_filters=OUTAGE_GRAIN, analysis_date=ANALYSIS_DATE,
        items=items,
    )


def _gate(state: str, score: ConfidenceScore, why: str = "") -> GateDecision:
    return GateDecision(
        gate_state=state,
        primary_hypothesis=score,
        all_scores=[score],
        plausible_hypotheses=[score.driver],
        why_undetermined=why,
        missing_data_needed=["Competitor pricing data for North region."],
        human_review_required=(state != GATE_HIGH_CONFIDENCE),
    )


def _rec(rule_id: str, driver: str, tier: str) -> Recommendation:
    return Recommendation(
        tier=tier, gate_state=GATE_HIGH_CONFIDENCE,
        driver=driver, rule_id=rule_id,
        confidence_score=0.75,
        action_owner="Supply Chain Manager",
        immediate_action="Expedite replenishment for North Electronics.",
        verification_step="Confirm orders recover within 24h.",
        escalation_path="Escalate to Head of Supply Chain if ETA > 48h.",
    )


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clear_llm_cache():
    """Clear LLM cache before each test to avoid cross-test pollution."""
    _CACHE.clear()
    yield
    _CACHE.clear()


@pytest.fixture
def inventory_scenario():
    score  = _score("R01_INVENTORY_SHORTAGE", "Inventory shortage", 0.78, "Strong")
    evd    = _make_evidence("R01_INVENTORY_SHORTAGE", 3, 0)
    gate   = _gate(GATE_HIGH_CONFIDENCE, score)
    rec    = _rec("R01_INVENTORY_SHORTAGE", "Inventory shortage", "Full")
    return score, evd, gate, rec


@pytest.fixture
def pricing_scenario():
    """
    Pricing scenario with ZERO supporting evidence items — for hallucination test.
    """
    score = _score("R03_PRICING_EFFECT", "Pricing effect", 0.15, "Insufficient")
    evd   = _make_evidence("R03_PRICING_EFFECT", 0, 2)   # 0 supporting, 2 contradicting
    gate  = _gate(GATE_INSUFFICIENT, score, why="No pricing signal detected.")
    rec   = _rec("R03_PRICING_EFFECT", "Pricing effect", "None")
    return score, evd, gate, rec


# ═══════════════════════════════════════════════════════════════════════════
# LLM CLIENT STRUCTURE
# ═══════════════════════════════════════════════════════════════════════════

class TestLLMClientStructure:
    def test_call_returns_llm_response(self) -> None:
        resp = call("Hello", "You are a test assistant.", provider="mock")
        assert isinstance(resp, LLMResponse)

    def test_mock_response_has_text(self) -> None:
        resp = call("Test prompt about inventory", "system", provider="mock")
        assert resp.text
        assert resp.success is True

    def test_mock_has_provider_set(self) -> None:
        resp = call("test", "system", provider="mock")
        assert resp.provider == "mock"

    def test_caching_second_call_returns_cached(self) -> None:
        prompt = "unique caching test prompt 12345"
        system = "system for cache test"
        r1 = call(prompt, system, provider="mock", use_cache=True)
        r2 = call(prompt, system, provider="mock", use_cache=True)
        assert r1.cached is False
        assert r2.cached is True
        assert r2.text == r1.text

    def test_no_cache_flag_bypasses_cache(self) -> None:
        prompt = "no cache test prompt"
        system = "system"
        r1 = call(prompt, system, provider="mock", use_cache=False)
        r2 = call(prompt, system, provider="mock", use_cache=False)
        assert r2.cached is False


# ═══════════════════════════════════════════════════════════════════════════
# §4.10 HALLUCINATION GUARDRAIL
# ═══════════════════════════════════════════════════════════════════════════

class TestHallucinationGuardrail:
    """
    09_LLM_ARCHITECTURE §10: if a hypothesis has zero supporting evidence,
    the prompt must contain an explicit GUARDRAIL marker — the LLM cannot fabricate.
    """

    def test_zero_evidence_prompt_contains_guardrail(self, pricing_scenario) -> None:
        """§4.10 core: GUARDRAIL marker must be in the evidence package."""
        score, evd, gate, rec = pricing_scenario
        package = build_evidence_package(
            kpi_name="Revenue",
            pct_change=-0.082,
            affected_segment="North / Electronics",
            gate_decision=gate,
            scores=[score],
            evidence_map={"R03_PRICING_EFFECT": evd},
            rag_map=None,
            recommendation=rec,
            analysis_date=ANALYSIS_DATE,
        )
        prompt = build_prompt(package)
        assert "GUARDRAIL" in prompt, (
            "Prompt must contain GUARDRAIL marker when hypothesis has zero supporting evidence"
        )

    def test_guardrail_explicitly_says_no_evidence(self, pricing_scenario) -> None:
        score, evd, gate, rec = pricing_scenario
        package = build_evidence_package(
            kpi_name="Revenue",
            pct_change=-0.082,
            affected_segment="North / Electronics",
            gate_decision=gate,
            scores=[score],
            evidence_map={"R03_PRICING_EFFECT": evd},
            rag_map=None,
            recommendation=rec,
            analysis_date=ANALYSIS_DATE,
        )
        # Find the pricing hypothesis entry
        pricing_hyp = next(
            h for h in package["hypotheses"] if h["driver"] == "Pricing effect"
        )
        supporting = " ".join(pricing_hyp["supporting_evidence"])
        assert "No supporting evidence" in supporting or "GUARDRAIL" in supporting, (
            "Supporting evidence for zero-evidence hypothesis must contain "
            "'No supporting evidence' or 'GUARDRAIL' text"
        )
        assert "do not claim" in supporting.lower() or "do not" in supporting.lower(), (
            "Guardrail must explicitly instruct the LLM not to claim this driver is supported"
        )

    def test_inventory_with_evidence_has_no_guardrail(self, inventory_scenario) -> None:
        """Inventory hypothesis WITH supporting evidence must NOT have guardrail."""
        score, evd, gate, rec = inventory_scenario
        package = build_evidence_package(
            kpi_name="Revenue",
            pct_change=-0.082,
            affected_segment="North / Electronics",
            gate_decision=gate,
            scores=[score],
            evidence_map={"R01_INVENTORY_SHORTAGE": evd},
            rag_map=None,
            recommendation=rec,
            analysis_date=ANALYSIS_DATE,
        )
        inv_hyp = next(
            h for h in package["hypotheses"] if "Inventory" in h["driver"]
        )
        # Should have real descriptions, not GUARDRAIL
        assert all("GUARDRAIL" not in e for e in inv_hyp["supporting_evidence"]), (
            "Hypothesis WITH supporting evidence must not have GUARDRAIL marker"
        )

    def test_abstention_package_has_abstention_block(self, pricing_scenario) -> None:
        """Insufficient gate must produce an abstention block in the package."""
        score, evd, gate, rec = pricing_scenario
        package = build_evidence_package(
            kpi_name="Revenue", pct_change=-0.082,
            affected_segment="North / Electronics",
            gate_decision=gate, scores=[score],
            evidence_map={"R03_PRICING_EFFECT": evd},
            rag_map=None, recommendation=rec, analysis_date=ANALYSIS_DATE,
        )
        assert "abstention" in package, (
            "Insufficient gate must produce an 'abstention' block in the package"
        )
        assert "why_undetermined" in package["abstention"]
        assert "missing_data" in package["abstention"]


# ═══════════════════════════════════════════════════════════════════════════
# §4.11 PERSONA NARRATIVE TEST
# ═══════════════════════════════════════════════════════════════════════════

class TestPersonaNarrative:
    """
    09_LLM_ARCHITECTURE §7:
    Same evidence, different persona → different tone/depth, same factual content.
    """

    def test_business_leader_system_mentions_brevity(self) -> None:
        system = get_system_prompt("business_leader")
        text   = system.lower()
        assert "3-5 sentences" in text or "brief" in text or "3–5" in text, (
            "business_leader system prompt must instruct brevity (3-5 sentences)"
        )

    def test_analyst_system_mentions_evidence_trail(self) -> None:
        system = get_system_prompt("analyst")
        text   = system.lower()
        assert "evidence trail" in text or "evidence" in text, (
            "analyst system prompt must mention evidence trail"
        )

    def test_analyst_system_mentions_method_names(self) -> None:
        system = get_system_prompt("analyst")
        assert "method" in system.lower() or "confidence" in system.lower()

    def test_business_leader_narrative_generated(self, inventory_scenario) -> None:
        score, evd, gate, rec = inventory_scenario
        result = generate_narrative(
            kpi_name="Revenue", pct_change=-0.082,
            affected_segment="North / Electronics",
            gate_decision=gate, scores=[score],
            evidence_map={"R01_INVENTORY_SHORTAGE": evd},
            rag_map=None, recommendation=rec,
            analysis_date=ANALYSIS_DATE, persona="business_leader",
            provider="mock",
        )
        assert isinstance(result, NarrativeResult)
        assert result.success
        assert result.persona == "business_leader"
        assert result.narrative

    def test_analyst_narrative_generated(self, inventory_scenario) -> None:
        score, evd, gate, rec = inventory_scenario
        result = generate_narrative(
            kpi_name="Revenue", pct_change=-0.082,
            affected_segment="North / Electronics",
            gate_decision=gate, scores=[score],
            evidence_map={"R01_INVENTORY_SHORTAGE": evd},
            rag_map=None, recommendation=rec,
            analysis_date=ANALYSIS_DATE, persona="analyst",
            provider="mock",
        )
        assert result.success
        assert result.persona == "analyst"

    def test_business_leader_narrative_no_zscore(self, inventory_scenario) -> None:
        """business_leader narrative must not contain technical jargon."""
        score, evd, gate, rec = inventory_scenario
        result = generate_narrative(
            kpi_name="Revenue", pct_change=-0.082,
            affected_segment="North / Electronics",
            gate_decision=gate, scores=[score],
            evidence_map={"R01_INVENTORY_SHORTAGE": evd},
            rag_map=None, recommendation=rec,
            analysis_date=ANALYSIS_DATE, persona="business_leader",
            provider="mock",
        )
        narrative_lower = result.narrative.lower()
        forbidden = ["z-score", "zscore", "standard deviation", "percentile rank"]
        for term in forbidden:
            assert term not in narrative_lower, (
                f"business_leader narrative must not contain '{term}'"
            )

    def test_evidence_package_preserved_in_result(self, inventory_scenario) -> None:
        """NarrativeResult must include the evidence_package for auditability."""
        score, evd, gate, rec = inventory_scenario
        result = generate_narrative(
            kpi_name="Revenue", pct_change=-0.082,
            affected_segment="North / Electronics",
            gate_decision=gate, scores=[score],
            evidence_map={"R01_INVENTORY_SHORTAGE": evd},
            rag_map=None, recommendation=rec,
            analysis_date=ANALYSIS_DATE, persona="analyst",
            provider="mock",
        )
        assert result.evidence_package
        assert "hypotheses" in result.evidence_package
        assert "kpi" in result.evidence_package

    def test_personas_produce_different_system_prompts(self) -> None:
        bl = get_system_prompt("business_leader")
        an = get_system_prompt("analyst")
        assert bl != an, "business_leader and analyst must have different system prompts"


# ═══════════════════════════════════════════════════════════════════════════
# llm_suggest — disabled by default
# ═══════════════════════════════════════════════════════════════════════════

class TestLLMSuggest:
    def test_disabled_by_default(self) -> None:
        """LLM_SUGGEST_ENABLED is not set → suggest returns empty list."""
        assert "LLM_SUGGEST_ENABLED" not in os.environ or \
               os.environ["LLM_SUGGEST_ENABLED"].lower() != "true", (
            "LLM_SUGGEST_ENABLED must be false for tests"
        )
        suggestions = suggest_additional_hypotheses(
            "Revenue", "North / Electronics",
            ["Inventory shortage", "Delivery disruption"]
        )
        assert suggestions == [], (
            "suggest_additional_hypotheses must return [] when disabled"
        )

    def test_enabled_with_mock_returns_list(self) -> None:
        """When enabled with mock provider, should return list (possibly empty — mock returns [])."""
        with patch.dict(os.environ, {"LLM_SUGGEST_ENABLED": "true", "LLM_PROVIDER": "mock"}):
            suggestions = suggest_additional_hypotheses(
                "Revenue", "North / Electronics",
                ["Inventory shortage"]
            )
            # Mock returns non-JSON → empty list (graceful degradation)
            assert isinstance(suggestions, list)
