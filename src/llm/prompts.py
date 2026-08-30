"""
src/llm/prompts.py — Prompt construction for TraceIQ's LLM layer.

Per 09_LLM_ARCHITECTURE.md §5-6:
  - LLM receives a pre-validated evidence package (never raw tables)
  - System prompt includes behavioral rules (§6) — no invention, no softening
  - Persona-specific instructions adapt tone/depth without altering facts (§7)

The hallucination guardrail is enforced HERE in prompt construction:
  - If a hypothesis has zero supporting evidence items, the prompt explicitly states
    "No supporting evidence exists for [driver]" — the LLM cannot fabricate any.
  - If the gate_state is Insufficient, the prompt contains the exact abstention
    text and forbids naming a cause.

Per CLAUDE.md §4: system instructions are templates, not hardcoded per-call strings.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Optional

from src.ambiguity.gate import GateDecision, GATE_INSUFFICIENT, format_abstention_output
from src.confidence.scoring import ConfidenceScore
from src.evidence.structured_evidence import StructuredEvidence
from src.evidence.retrieval import RAGResult
from src.recommendation.recommendation_engine import Recommendation


# ── System prompt templates per persona (09_LLM_ARCHITECTURE §7) ──────────

_SYSTEM_BUSINESS_LEADER = """\
You are TraceIQ, an AI business intelligence assistant.
Your job is to explain a KPI anomaly to a senior business leader in plain English.

Rules you MUST follow (non-negotiable):
1. Lead with business impact and a clear action. Keep to 3-5 sentences maximum.
2. Use only the evidence in the package below — do not invent causes, numbers, or evidence.
3. If a hypothesis shows "confidence: Insufficient", state plainly that it cannot be confirmed \
and name the missing data — do not imply it might still be the cause.
4. Do not mention z-scores, statistical methods, or technical model names.
5. If the package contains an abstention (gate_state = Insufficient), say \
"We cannot determine the cause yet" and state what data is needed. Do not name a driver.
6. Only phrase a recommendation if one is included in the package — never invent one.
"""

_SYSTEM_ANALYST = """\
You are TraceIQ, an AI business intelligence assistant.
Your job is to explain a KPI anomaly to an operations analyst with full technical detail.

Rules you MUST follow (non-negotiable):
1. Include the full evidence trail: metric names, deltas, baseline comparisons, \
RAG similarity scores where present, and confidence reasoning.
2. Use only the evidence in the package — do not invent causes, numbers, or evidence.
3. Cite the dominant factor, localized problem statement, and decomposition results.
4. If a hypothesis shows "confidence: Insufficient", state that plainly and name the \
missing data and next investigation step.
5. If the package contains an abstention, list all plausible hypotheses and the \
specific data gaps.
6. Only phrase a recommendation if one is included in the package — never invent one.
"""

_SYSTEM_PERSONAS = {
    "business_leader": _SYSTEM_BUSINESS_LEADER,
    "analyst":         _SYSTEM_ANALYST,
}

# Default persona if unrecognised
_DEFAULT_PERSONA = "business_leader"


# ── Evidence package builder ───────────────────────────────────────────────

def build_evidence_package(
    kpi_name: str,
    pct_change: float,
    affected_segment: str,
    gate_decision: GateDecision,
    scores: list[ConfidenceScore],
    evidence_map: dict[str, StructuredEvidence],
    rag_map: Optional[dict[str, RAGResult]],
    recommendation: Recommendation,
    analysis_date: date,
) -> dict:
    """
    Build the pre-validated evidence package (09_LLM_ARCHITECTURE §5).
    This is the ONLY input the LLM sees — no raw tables, no SQL results.

    Hallucination guardrail: for each hypothesis, if supporting_evidence is empty,
    we insert an explicit "No supporting evidence" marker.
    """
    hypotheses_list = []
    for score in scores:
        evd   = evidence_map.get(score.hypothesis_rule_id)
        rag   = (rag_map or {}).get(score.hypothesis_rule_id)

        # Supporting evidence items → human-readable strings
        supporting: list[str] = []
        if evd:
            for item in evd.supporting:
                supporting.append(item.description)
        if rag and rag.has_supporting_evidence:
            top = rag.top_snippet
            if top:
                supporting.append(
                    f"Unstructured evidence: '{top.text[:120]}...' "
                    f"(similarity={top.similarity_score:.2f}, source={top.source})"
                )

        # Contradicting evidence
        contradicting: list[str] = []
        if evd:
            for item in evd.contradicting:
                contradicting.append(item.description)

        # ── HALLUCINATION GUARDRAIL ───────────────────────────────────────
        # If there is genuinely zero supporting evidence, say so explicitly.
        # The LLM cannot fabricate evidence it was not given.
        if not supporting:
            supporting = [
                f"[GUARDRAIL] No supporting evidence exists for '{score.driver}' "
                "in the structured or unstructured data for this alert. "
                "Do not claim this driver is supported."
            ]

        missing_data = ""
        if score.label == "Insufficient":
            # Pull from gate decision's missing data list
            missing_data = "; ".join(gate_decision.missing_data_needed[:2])

        hyp_entry = {
            "driver":               score.driver,
            "confidence":           score.label,
            "score":                round(score.raw_score, 3),
            "supporting_evidence":  supporting,
            "conflicting_evidence": contradicting,
        }
        if missing_data:
            hyp_entry["missing_data"] = missing_data

        hypotheses_list.append(hyp_entry)

    # Recommendation block (only if actionable)
    rec_block: Optional[dict] = None
    if recommendation.is_actionable:
        rec_block = {
            "tier":             recommendation.tier,
            "action_owner":     recommendation.action_owner,
            "immediate_action": recommendation.immediate_action,
            "verification":     recommendation.verification_step,
            "escalation":       recommendation.escalation_path,
        }
        if recommendation.tier == "Conditional":
            rec_block["condition_for_full"] = recommendation.condition_for_full

    package = {
        "kpi":              kpi_name,
        "change":           f"{pct_change * 100:+.1f}%",
        "significance":     "High" if abs(pct_change) > 0.10 else "Moderate",
        "affected_segment": affected_segment,
        "analysis_date":    str(analysis_date),
        "gate_state":       gate_decision.gate_state,
        "hypotheses":       hypotheses_list,
    }
    if rec_block:
        package["recommendation"] = rec_block
    if gate_decision.should_abstain:
        package["abstention"] = {
            "why_undetermined":   gate_decision.why_undetermined,
            "missing_data":       gate_decision.missing_data_needed,
            "next_step":          gate_decision.next_step,
            "plausible_hypotheses": gate_decision.plausible_hypotheses,
        }

    return package


def build_prompt(evidence_package: dict) -> str:
    """Convert evidence package to the user-facing prompt sent to the LLM."""
    return (
        "Here is the evidence package for this alert. "
        "Generate a narrative based ONLY on this data:\n\n"
        + json.dumps(evidence_package, indent=2)
    )


def get_system_prompt(persona: str) -> str:
    """Get the system prompt for the given persona."""
    return _SYSTEM_PERSONAS.get(persona, _SYSTEM_PERSONAS[_DEFAULT_PERSONA])
