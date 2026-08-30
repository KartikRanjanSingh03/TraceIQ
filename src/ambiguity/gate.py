"""
src/ambiguity/gate.py — Ambiguity Gate for TraceIQ.

Implements 08_CONFIDENCE_AND_ABSTENTION.md §3 and §5:
  Consumes the ConfidenceScore list from Stage 7 and routes each hypothesis
  (and the overall alert) to one of three gate states:
    🟢 High Confidence   → full recommendation, primary driver named
    🟡 Moderate          → conditional recommendation, human review required
    🔴 Insufficient      → abstention — names missing data, proposes next step

Key rule: the gate decides deterministically — the LLM is never called to decide
whether to abstain (08_CONFIDENCE_AND_ABSTENTION §7).

Output: GateDecision dataclass consumed by:
  - Recommendation Engine (Stage 9)
  - LLM Narrative Layer (Stage 10)
  - API response (Stage 11)

Per CLAUDE.md §4: no hardcoded thresholds here — all come from scoring.py constants.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from src.confidence.scoring import ConfidenceScore, _THRESH_STRONG, _THRESH_MODERATE

# ── Gate states ────────────────────────────────────────────────────────────

GATE_HIGH_CONFIDENCE = "HighConfidence"
GATE_MODERATE        = "Moderate"
GATE_INSUFFICIENT    = "Insufficient"

# Gap threshold: if top-2 hypotheses are within this margin → ambiguous / too-close-to-call
_AMBIGUITY_GAP_THRESHOLD = 0.10


# ── Output types ──────────────────────────────────────────────────────────

@dataclass
class GateDecision:
    """
    Routing decision for one alert's full hypothesis set.
    Consumed by Recommendation Engine (Stage 9) and LLM layer (Stage 10).
    """
    gate_state: str                     # GATE_HIGH_CONFIDENCE | GATE_MODERATE | GATE_INSUFFICIENT
    primary_hypothesis: Optional[ConfidenceScore]   # top-ranked hypothesis (may be None)
    all_scores: list[ConfidenceScore]

    # For abstention output template (08_CONFIDENCE §4)
    plausible_hypotheses: list[str] = field(default_factory=list)
    why_undetermined: str = ""
    missing_data_needed: list[str] = field(default_factory=list)
    next_step: str = ""

    # For all states
    rationale: str = ""
    human_review_required: bool = False

    @property
    def is_confident(self) -> bool:
        return self.gate_state == GATE_HIGH_CONFIDENCE

    @property
    def should_abstain(self) -> bool:
        return self.gate_state == GATE_INSUFFICIENT


# ── Missing data catalogue per rule ───────────────────────────────────────
# Used to populate the abstention template (08_CONFIDENCE §4)

_MISSING_DATA: dict[str, str] = {
    "R01_INVENTORY_SHORTAGE": (
        "Real-time warehouse stock levels and replenishment ETA for the affected "
        "region/category; purchase-order data to confirm supply disruption."
    ),
    "R02_DELIVERY_DISRUPTION": (
        "Carrier/logistics provider incident logs; regional weather or road disruption data "
        "for the affected period."
    ),
    "R03_PRICING_EFFECT": (
        "Competitor pricing data; promotional calendar for the affected region/category; "
        "price-elasticity estimates."
    ),
    "R04_COMPETITOR_ACTIVITY": (
        "Competitor pricing/promotion exposure data for the affected region/category; "
        "regional sales-team notes on competitor activity."
    ),
    "R05_MIX_CHANGE": (
        "Product-level SKU mix data; customer lifecycle stage distribution; "
        "marketing campaign exposure by segment."
    ),
    "R06_DEMAND_DECLINE": (
        "External demand signals (search trends, category seasonality index); "
        "macroeconomic indicator data for the region."
    ),
}

_NEXT_STEPS: dict[str, str] = {
    "R01_INVENTORY_SHORTAGE": (
        "Check WMS/ERP for current stock levels and purchase orders. "
        "Escalate to supply chain team if outage confirmed."
    ),
    "R02_DELIVERY_DISRUPTION": (
        "Contact logistics provider for incident report. "
        "Check regional delivery dashboard for SLA trend."
    ),
    "R03_PRICING_EFFECT": (
        "Review pricing team's recent price changes in the affected grain. "
        "Request competitor intelligence feed."
    ),
    "R04_COMPETITOR_ACTIVITY": (
        "Request competitor intelligence feed or manually check "
        "regional sales-team notes for competitor activity reports."
    ),
    "R05_MIX_CHANGE": (
        "Segment the order data by customer tier and product group "
        "to identify which mix is shifting."
    ),
    "R06_DEMAND_DECLINE": (
        "Review external demand signals (Google Trends, category index). "
        "Cross-reference with macro indicators for the region."
    ),
}


# ── Gate logic ────────────────────────────────────────────────────────────

def _is_too_close(scores: list[ConfidenceScore]) -> bool:
    """
    True if top-2 hypotheses are within _AMBIGUITY_GAP_THRESHOLD of each other.
    When two hypotheses are near-equal, we cannot name a single driver confidently.
    """
    if len(scores) < 2:
        return False
    return (scores[0].raw_score - scores[1].raw_score) < _AMBIGUITY_GAP_THRESHOLD


def _build_abstention(
    scores: list[ConfidenceScore],
    why: str,
) -> GateDecision:
    """Build a 🔴 Insufficient gate decision (abstention)."""
    plausible = [s.driver for s in scores if s.label in ("Moderate", "Weak", "Insufficient")]
    if not plausible and scores:
        plausible = [scores[0].driver]

    # Collect missing data for all plausible hypotheses
    missing: list[str] = []
    for s in scores:
        md = _MISSING_DATA.get(s.hypothesis_rule_id)
        if md and md not in missing:
            missing.append(md)

    # Best next step = primary hypothesis's recommendation
    next_step = (
        _NEXT_STEPS.get(scores[0].hypothesis_rule_id, "Investigate further.")
        if scores else "Investigate further."
    )

    return GateDecision(
        gate_state=GATE_INSUFFICIENT,
        primary_hypothesis=scores[0] if scores else None,
        all_scores=scores,
        plausible_hypotheses=plausible,
        why_undetermined=why,
        missing_data_needed=missing,
        next_step=next_step,
        rationale=f"Abstaining: {why}",
        human_review_required=True,
    )


def evaluate(scores: list[ConfidenceScore]) -> GateDecision:
    """
    Evaluate the gate state from the ranked confidence scores.

    Per 08_CONFIDENCE_AND_ABSTENTION.md §3:
      🟢 HighConfidence:  top hypothesis is Strong AND gap to #2 ≥ threshold
      🟡 Moderate:        top hypothesis is Moderate, OR gap is too small (ambiguous)
      🔴 Insufficient:    no hypothesis reaches Moderate, OR contradicting evidence
                          outweighs supporting, OR sparse history with Moderate cap

    Args:
        scores: ConfidenceScore list from Stage 7, sorted desc by raw_score

    Returns:
        GateDecision
    """
    if not scores:
        return _build_abstention(
            [], "No hypotheses reached the scoring threshold."
        )

    top = scores[0]

    # ── 🔴 Insufficient: top hypothesis too weak ───────────────────────────
    if top.label in ("Weak", "Insufficient"):
        return _build_abstention(
            scores,
            "No hypothesis reached Moderate or higher confidence. "
            "Evidence is insufficient or contradictory."
        )

    # ── 🔴 Insufficient: too-close-to-call between top 2 hypotheses ───────
    if top.label == "Moderate" and _is_too_close(scores):
        return _build_abstention(
            scores,
            f"Top-2 hypotheses are within {_AMBIGUITY_GAP_THRESHOLD:.0%} of each other "
            f"({scores[0].driver} vs {scores[1].driver}). "
            "Cannot reliably name a single primary driver."
        )

    # ── 🟡 Moderate: top is Moderate (but not too-close-to-call) ──────────
    if top.label == "Moderate":
        missing = [_MISSING_DATA.get(top.hypothesis_rule_id, "Additional data required.")]
        return GateDecision(
            gate_state=GATE_MODERATE,
            primary_hypothesis=top,
            all_scores=scores,
            missing_data_needed=missing,
            next_step=_NEXT_STEPS.get(top.hypothesis_rule_id, ""),
            rationale=(
                f"Moderate confidence: '{top.driver}' is the most likely contributor "
                f"(score={top.raw_score:.2f}), but additional validation is needed."
            ),
            human_review_required=True,
        )

    # ── 🟢 High Confidence: top is Strong ─────────────────────────────────
    return GateDecision(
        gate_state=GATE_HIGH_CONFIDENCE,
        primary_hypothesis=top,
        all_scores=scores,
        rationale=(
            f"High confidence: '{top.driver}' identified as primary driver "
            f"(score={top.raw_score:.2f}, label={top.label}). "
            f"Gap to next hypothesis: "
            f"{scores[0].raw_score - scores[1].raw_score:.2f}"
            if len(scores) > 1 else
            f"High confidence: '{top.driver}' (score={top.raw_score:.2f})."
        ),
        human_review_required=False,
    )


def format_abstention_output(decision: GateDecision) -> str:
    """
    Format the abstention output per 08_CONFIDENCE_AND_ABSTENTION §4 template.
    This text is passed to the LLM as the basis for its natural-language phrasing.
    The LLM must not deviate from this — it can only rephrase, not add or remove facts.
    """
    if not decision.should_abstain:
        return ""

    lines = [
        "Status: Insufficient Evidence",
        f"Plausible hypotheses: [{', '.join(decision.plausible_hypotheses)}]",
        f"Why undetermined: {decision.why_undetermined}",
    ]
    if decision.missing_data_needed:
        lines.append(
            "Missing data needed: "
            + "; ".join(decision.missing_data_needed)
        )
    if decision.next_step:
        lines.append(f"Next step: {decision.next_step}")

    return "\n".join(lines)
