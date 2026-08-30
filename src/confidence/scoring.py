"""
src/confidence/scoring.py — Confidence Engine for TraceIQ.

Implements 07_ANALYTICS_AND_DRIVER_ANALYSIS.md §6.1 (evidence weighting):
  For each hypothesis, compute a confidence score from:
    1. Rule signal_strength (from Stage 4)
    2. Structured evidence: ratio of supporting vs contradicting items (Stage 5)
    3. RAG evidence: top snippet similarity + supporting flag (Stage 6)
    4. Penalties: sparse_history flag, contradicting evidence, missing data

Output labels (per 08_CONFIDENCE_AND_ABSTENTION.md §3):
  Strong          → top recommendation, full narrative
  Moderate        → conditional recommendation, flagged for human review
  Weak            → presented but not actioned
  Insufficient    → abstention (handled by Ambiguity Gate, Stage 8)

Per CLAUDE.md §4: all weights configurable — no hardcoded magic numbers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from src.evidence.retrieval import RAGResult
from src.evidence.structured_evidence import StructuredEvidence
from src.hypothesis.rules import HypothesisCandidate

# ── Weight configuration ──────────────────────────────────────────────────
# These are "configured, reasoned defaults" per 08_CONFIDENCE_AND_ABSTENTION §8.
# They are not statistically calibrated; the Feedback Loop (Stage 13) is the
# mechanism by which they'd be refined in production.

_W_RULE_SIGNAL     = 0.35   # weight: rule signal_strength (Stage 4)
_W_STRUCTURED_EVD  = 0.40   # weight: structured evidence quality (Stage 5)
_W_RAG_EVD         = 0.25   # weight: RAG top-snippet similarity (Stage 6)

_PENALTY_SPARSE       = 0.20   # cap drop for sparse_history (per §2.6)
_PENALTY_CONTRADICT   = 0.10   # per contradicting evidence item (capped at 0.30)
_PENALTY_NO_RAG       = 0.10   # if RAG result is empty (missing unstructured evidence)

# Label thresholds
_THRESH_STRONG       = 0.65
_THRESH_MODERATE     = 0.40
_THRESH_WEAK         = 0.20
# < _THRESH_WEAK → Insufficient


# ── Output types ──────────────────────────────────────────────────────────

@dataclass
class ConfidenceScore:
    """
    Confidence assessment for one hypothesis.
    Consumed by the Ambiguity Gate (Stage 8) and Recommendation Engine (Stage 9).
    """
    hypothesis_rule_id: str
    driver: str
    raw_score: float            # 0.0–1.0 weighted composite
    label: str                  # "Strong" | "Moderate" | "Weak" | "Insufficient"

    # Breakdown for transparency
    rule_signal_component: float
    structured_evidence_component: float
    rag_component: float
    penalties_applied: float

    supporting_count: int
    contradicting_count: int
    rag_top_score: float
    sparse_history: bool

    rationale: list[str] = field(default_factory=list)   # human-readable breakdown


# ── Scoring helpers ────────────────────────────────────────────────────────

def _score_structured_evidence(evidence: StructuredEvidence) -> tuple[float, int, int]:
    """
    Returns (score 0-1, supporting_count, contradicting_count).
    Score = supporting / (supporting + contradicting + epsilon)
    Each item contributes proportionally.
    """
    n_support = len(evidence.supporting)
    n_contra  = len(evidence.contradicting)
    total     = n_support + n_contra

    if total == 0:
        return 0.0, 0, 0

    raw = n_support / total                         # 0 if all contradicting, 1 if all supporting

    # Boost if average supporting EvidenceItem delta is large
    avg_sup_delta = (
        sum(abs(e.delta) for e in evidence.supporting) / n_support
        if n_support > 0 else 0.0
    )
    boost = min(0.20, avg_sup_delta / 1000)         # small boost for large absolute deltas
    return min(1.0, raw + boost), n_support, n_contra


def _score_rag(rag: Optional[RAGResult]) -> float:
    """
    Returns 0.0–1.0 based on the top snippet's similarity score.
    0.0 if no RAG result or no snippets.
    """
    if rag is None or not rag.snippets:
        return 0.0
    top = rag.top_snippet
    if top is None:
        return 0.0
    # Scale: similarity already 0-1; cap at 0.95 to prevent over-weighting
    return min(0.95, top.similarity_score)


def _label(score: float) -> str:
    if score >= _THRESH_STRONG:
        return "Strong"
    if score >= _THRESH_MODERATE:
        return "Moderate"
    if score >= _THRESH_WEAK:
        return "Weak"
    return "Insufficient"


# ── Public API ────────────────────────────────────────────────────────────

def score_hypothesis(
    hypothesis: HypothesisCandidate,
    structured_evidence: StructuredEvidence,
    rag_result: Optional[RAGResult] = None,
    *,
    sparse_history: bool = False,
) -> ConfidenceScore:
    """
    Compute a composite confidence score for one hypothesis.

    Args:
        hypothesis:          Fired HypothesisCandidate from Stage 4
        structured_evidence: StructuredEvidence from Stage 5
        rag_result:          RAGResult from Stage 6 (optional — None if not yet built)
        sparse_history:      True if the alert grain had sparse history (Stage 2 flag)

    Returns:
        ConfidenceScore with label and per-component breakdown
    """
    rationale: list[str] = []

    # ── Component 1: Rule signal ──────────────────────────────────────────
    rule_comp = hypothesis.signal_strength * _W_RULE_SIGNAL
    rationale.append(
        f"Rule signal: {hypothesis.signal_strength:.2f} × {_W_RULE_SIGNAL} "
        f"= {rule_comp:.3f}"
    )

    # ── Component 2: Structured evidence ─────────────────────────────────
    evd_score, n_sup, n_con = _score_structured_evidence(structured_evidence)
    evd_comp = evd_score * _W_STRUCTURED_EVD
    rationale.append(
        f"Structured evidence: {n_sup} supporting, {n_con} contradicting → "
        f"score {evd_score:.2f} × {_W_STRUCTURED_EVD} = {evd_comp:.3f}"
    )

    # ── Component 3: RAG evidence ─────────────────────────────────────────
    rag_score = _score_rag(rag_result)
    rag_comp  = rag_score * _W_RAG_EVD
    rag_top   = rag_result.top_snippet.similarity_score if (
        rag_result and rag_result.top_snippet
    ) else 0.0
    rationale.append(
        f"RAG evidence: top similarity {rag_top:.3f} × {_W_RAG_EVD} "
        f"= {rag_comp:.3f}"
    )

    # ── Penalties ─────────────────────────────────────────────────────────
    penalties = 0.0

    # Penalty: sparse history
    if sparse_history:
        penalties += _PENALTY_SPARSE
        rationale.append(
            f"Penalty: sparse history (−{_PENALTY_SPARSE}) — "
            "confidence capped to Moderate per §2.6"
        )

    # Penalty: contradicting evidence items
    contra_penalty = min(0.30, n_con * _PENALTY_CONTRADICT)
    if contra_penalty > 0:
        penalties += contra_penalty
        rationale.append(
            f"Penalty: {n_con} contradicting items (−{contra_penalty:.2f})"
        )

    # Penalty: no RAG result
    if rag_result is None or not rag_result.snippets:
        penalties += _PENALTY_NO_RAG
        rationale.append(
            f"Penalty: no RAG snippets retrieved (−{_PENALTY_NO_RAG})"
        )

    # ── Composite score ───────────────────────────────────────────────────
    raw_score = max(0.0, min(1.0, rule_comp + evd_comp + rag_comp - penalties))
    label     = _label(raw_score)

    # Sparse history hard-cap: cannot be Strong (per 07_ANALYTICS §2.6)
    if sparse_history and label == "Strong":
        label = "Moderate"
        rationale.append(
            "Label capped to Moderate due to sparse history (per 07_ANALYTICS §2.6)."
        )

    return ConfidenceScore(
        hypothesis_rule_id=hypothesis.rule_id,
        driver=hypothesis.driver,
        raw_score=raw_score,
        label=label,
        rule_signal_component=rule_comp,
        structured_evidence_component=evd_comp,
        rag_component=rag_comp,
        penalties_applied=penalties,
        supporting_count=n_sup,
        contradicting_count=n_con,
        rag_top_score=rag_top,
        sparse_history=sparse_history,
        rationale=rationale,
    )


def score_all(
    hypotheses: list[HypothesisCandidate],
    evidence_map: dict[str, StructuredEvidence],
    rag_map: Optional[dict[str, RAGResult]] = None,
    *,
    sparse_history: bool = False,
) -> list[ConfidenceScore]:
    """
    Score all fired hypotheses and return sorted by raw_score descending.

    Args:
        hypotheses:    All candidates from Stage 4 (fired and unfired)
        evidence_map:  {rule_id: StructuredEvidence} from Stage 5
        rag_map:       {rule_id: RAGResult} from Stage 6 (optional)
        sparse_history: from the alert's detection result

    Returns:
        List of ConfidenceScore for fired hypotheses only, sorted by raw_score desc.
    """
    rag_map = rag_map or {}
    scores: list[ConfidenceScore] = []

    for h in hypotheses:
        if not h.fired:
            continue
        evd = evidence_map.get(h.rule_id)
        if evd is None:
            continue
        rag = rag_map.get(h.rule_id)
        scores.append(score_hypothesis(h, evd, rag, sparse_history=sparse_history))

    scores.sort(key=lambda s: s.raw_score, reverse=True)
    return scores
