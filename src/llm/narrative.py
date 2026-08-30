"""
src/llm/narrative.py — Narrative generation orchestrator for TraceIQ.

Combines prompts.py + llm_client.py to generate persona-specific narratives.
Per 09_LLM_ARCHITECTURE §11: one LLM call per persona per alert.

Also exposes generate_narrative() which is the single entry point used by
the API layer (Stage 11) and Streamlit UI (Stage 12).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from src.ambiguity.gate import GateDecision
from src.confidence.scoring import ConfidenceScore
from src.evidence.retrieval import RAGResult
from src.evidence.structured_evidence import StructuredEvidence
from src.llm.llm_client import LLMResponse, call
from src.llm.prompts import (
    build_evidence_package,
    build_prompt,
    get_system_prompt,
)
from src.recommendation.recommendation_engine import Recommendation


# ── Output type ────────────────────────────────────────────────────────────

@dataclass
class NarrativeResult:
    """
    Full narrative output for one alert × one persona.
    Persisted to the pipeline for the API and UI.
    """
    persona: str
    narrative: str
    evidence_package: dict       # the validated JSON sent to the LLM
    llm_response: LLMResponse

    @property
    def success(self) -> bool:
        return self.llm_response.success


# ── Main entry point ────────────────────────────────────────────────────────

def generate_narrative(
    kpi_name: str,
    pct_change: float,
    affected_segment: str,
    gate_decision: GateDecision,
    scores: list[ConfidenceScore],
    evidence_map: dict[str, StructuredEvidence],
    rag_map: Optional[dict[str, RAGResult]],
    recommendation: Recommendation,
    analysis_date: date,
    *,
    persona: str = "business_leader",
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> NarrativeResult:
    """
    Generate a persona-specific narrative for one alert.

    Per 09_LLM_ARCHITECTURE §9: uses caching — identical evidence + persona = cached call.

    Args:
        kpi_name:          e.g. "Revenue"
        pct_change:        signed fractional change, e.g. -0.082
        affected_segment:  e.g. "North / Electronics"
        gate_decision:     from Stage 8
        scores:            from Stage 7 (sorted by raw_score desc)
        evidence_map:      {rule_id: StructuredEvidence} from Stage 5
        rag_map:           {rule_id: RAGResult} from Stage 6
        recommendation:    from Stage 9
        analysis_date:     alert date
        persona:           "business_leader" | "analyst"
        provider:          override LLM provider
        model:             override LLM model

    Returns:
        NarrativeResult
    """
    package = build_evidence_package(
        kpi_name=kpi_name,
        pct_change=pct_change,
        affected_segment=affected_segment,
        gate_decision=gate_decision,
        scores=scores,
        evidence_map=evidence_map,
        rag_map=rag_map,
        recommendation=recommendation,
        analysis_date=analysis_date,
    )

    prompt = build_prompt(package)
    system = get_system_prompt(persona)

    llm_resp = call(prompt, system, provider=provider, model=model, use_cache=True)

    return NarrativeResult(
        persona=persona,
        narrative=llm_resp.text,
        evidence_package=package,
        llm_response=llm_resp,
    )
