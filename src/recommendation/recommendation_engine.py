"""
src/recommendation/recommendation_engine.py — Recommendation Engine for TraceIQ.

Implements 02_ARCHITECTURE.md §3.9 and 17_ROADMAP.md Stage 9:
  Strong confidence    → full, specific, actionable recommendation
  Moderate confidence  → conditional recommendation (flagged for human review)
  Insufficient         → no recommendation; abstention text only

Recommendations are structured (not free text) — the LLM in Stage 10 uses
these as a factual basis for its narrative phrasing.

Per CLAUDE.md §4: no hardcoded content — recommendation templates keyed on rule_id,
pulled from the module's catalogue. Easily extensible.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from src.ambiguity.gate import GateDecision, GATE_HIGH_CONFIDENCE, GATE_MODERATE


# ── Output types ──────────────────────────────────────────────────────────

@dataclass
class Recommendation:
    """
    Structured recommendation for one alert.
    Consumed by the LLM narrative layer (Stage 10) and persisted to pipeline.kpi_alerts.
    """
    tier: str                       # "Full" | "Conditional" | "None"
    gate_state: str                 # from GateDecision
    driver: str                     # e.g. "Inventory shortage"
    rule_id: str                    # e.g. "R01_INVENTORY_SHORTAGE"
    confidence_score: float

    # Structured recommendation fields
    action_owner: str               # who should act
    immediate_action: str           # what to do within 24h
    verification_step: str          # how to verify the recommendation is correct
    escalation_path: str            # who to escalate to if action insufficient

    # Conditional tier only
    condition_for_full: str = ""    # what must be verified before full action
    human_review_note: str = ""

    # Metadata
    analysis_date: Optional[date] = None
    grain_filters: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def is_actionable(self) -> bool:
        return self.tier in ("Full", "Conditional")


# ── Recommendation catalogue per rule ─────────────────────────────────────

_RECOMMENDATIONS: dict[str, dict] = {
    "R01_INVENTORY_SHORTAGE": {
        "action_owner":       "Supply Chain Manager",
        "immediate_action": (
            "Confirm stockout via WMS for the affected region/category. "
            "Expedite replenishment order or request inter-warehouse transfer "
            "from nearest surplus location."
        ),
        "verification_step": (
            "Verify stock replenishment ETA via WMS. "
            "Confirm orders resume rising within 24h of restock."
        ),
        "escalation_path": (
            "Escalate to Head of Supply Chain if restock ETA > 48h. "
            "Notify Category Manager to consider temporary product substitution."
        ),
        "condition_for_full": (
            "Confirm WMS shows zero or near-zero stock for the affected SKUs "
            "before issuing full replenishment order."
        ),
    },
    "R02_DELIVERY_DISRUPTION": {
        "action_owner":       "Logistics Operations Manager",
        "immediate_action": (
            "Contact carrier/3PL for incident report. "
            "Identify impacted shipments and trigger SLA-breach compensation workflow."
        ),
        "verification_step": (
            "Confirm breach rate returns to baseline within 24h of carrier resolution."
        ),
        "escalation_path": (
            "Escalate to Head of Logistics if breach rate stays elevated beyond 24h."
        ),
        "condition_for_full": (
            "Verify carrier confirms an active incident affecting the North region "
            "before triggering full SLA-breach compensation workflow."
        ),
    },
    "R03_PRICING_EFFECT": {
        "action_owner":       "Category Pricing Manager",
        "immediate_action": (
            "Review recent price changes for the affected category/region. "
            "Consider targeted discount or promotional offer to restore demand."
        ),
        "verification_step": (
            "Run an A/B test with restored discount rate in a sub-segment. "
            "Confirm AOV and order volume response within 3 business days."
        ),
        "escalation_path": (
            "Escalate to Head of Commercial if demand does not recover within 5 days."
        ),
        "condition_for_full": (
            "Confirm price change magnitude and timing aligns with the demand drop "
            "before issuing discount restoration instruction."
        ),
    },
    "R04_COMPETITOR_ACTIVITY": {
        "action_owner":       "Category Manager / Commercial Intelligence",
        "immediate_action": (
            "Request competitor intelligence report from the sales team. "
            "Check competitor pricing in the affected category/region."
        ),
        "verification_step": (
            "Confirm competitor promotion is active and overlaps with the affected segment. "
            "Review market share data if available."
        ),
        "escalation_path": (
            "Escalate to Head of Commercial if competitor promotion confirmed "
            "and market share loss exceeds 2pp."
        ),
        "condition_for_full": (
            "Obtain confirmed competitor promotion data before issuing counter-promotion."
        ),
    },
    "R05_MIX_CHANGE": {
        "action_owner":       "Customer Analytics / Marketing Manager",
        "immediate_action": (
            "Analyse customer segment mix for the affected grain and identify "
            "which segments have declined most sharply."
        ),
        "verification_step": (
            "Confirm segment distribution shift is sustained over ≥3 days "
            "before treating as structural."
        ),
        "escalation_path": (
            "Escalate to Head of Marketing if premium segment attrition confirmed."
        ),
        "condition_for_full": (
            "Confirm mix shift is not a data artefact (holiday, one-off event) "
            "before treating as a structural trend."
        ),
    },
    "R06_DEMAND_DECLINE": {
        "action_owner":       "Category Manager",
        "immediate_action": (
            "Review external demand signals (search trends, category seasonality index). "
            "Check if any macroeconomic event is depressing demand in the region."
        ),
        "verification_step": (
            "Cross-reference with comparable regions showing no decline. "
            "Confirm demand drop persists for ≥5 days before structural intervention."
        ),
        "escalation_path": (
            "Escalate to Head of Category if demand decline exceeds 10% for >5 consecutive days."
        ),
        "condition_for_full": (
            "Rule out all supply-side causes before treating as pure demand decline."
        ),
    },
}


# ── Engine ────────────────────────────────────────────────────────────────

def generate_recommendation(
    gate_decision: GateDecision,
    analysis_date: Optional[date] = None,
    grain_filters: Optional[dict] = None,
) -> Recommendation:
    """
    Generate a structured recommendation from a GateDecision.

    Per the architecture:
      Strong   → Full recommendation (all fields populated, no condition caveat)
      Moderate → Conditional recommendation (condition_for_full populated)
      Insufficient → Tier = "None" (no recommendation, abstention only)

    Args:
        gate_decision:   From Stage 8 Ambiguity Gate
        analysis_date:   Alert date (for metadata)
        grain_filters:   Alert grain (for metadata)

    Returns:
        Recommendation dataclass
    """
    grain_filters = grain_filters or {}
    primary = gate_decision.primary_hypothesis

    # ── Insufficient: no recommendation ──────────────────────────────────
    if gate_decision.gate_state not in (GATE_HIGH_CONFIDENCE, GATE_MODERATE):
        driver  = primary.driver if primary else "Unknown"
        rule_id = primary.hypothesis_rule_id if primary else "UNKNOWN"
        template = _RECOMMENDATIONS.get(rule_id, {})
        return Recommendation(
            tier="None",
            gate_state=gate_decision.gate_state,
            driver=driver,
            rule_id=rule_id,
            confidence_score=primary.raw_score if primary else 0.0,
            action_owner="N/A",
            immediate_action=(
                "Insufficient evidence — no action recommended until "
                "additional data is collected. See abstention details."
            ),
            verification_step="Collect missing data listed in abstention output.",
            escalation_path=template.get("escalation_path", ""),
            condition_for_full="",
            human_review_note=gate_decision.why_undetermined,
            analysis_date=analysis_date,
            grain_filters=grain_filters,
            notes=["No recommendation issued: evidence insufficient."],
        )

    rule_id  = primary.hypothesis_rule_id
    driver   = primary.driver
    score    = primary.raw_score
    template = _RECOMMENDATIONS.get(rule_id, {})

    is_full = gate_decision.gate_state == GATE_HIGH_CONFIDENCE

    notes = []
    if not template:
        notes.append(f"No recommendation template found for {rule_id} — generic output used.")

    return Recommendation(
        tier="Full" if is_full else "Conditional",
        gate_state=gate_decision.gate_state,
        driver=driver,
        rule_id=rule_id,
        confidence_score=score,
        action_owner=template.get("action_owner", "Operations Team"),
        immediate_action=template.get("immediate_action", "Investigate further."),
        verification_step=template.get("verification_step", "Verify resolution."),
        escalation_path=template.get("escalation_path", "Escalate to senior management."),
        condition_for_full="" if is_full else template.get("condition_for_full", ""),
        human_review_note=(
            "" if is_full else
            "This is a conditional recommendation — human review required before action."
        ),
        analysis_date=analysis_date,
        grain_filters=grain_filters,
        notes=notes,
    )
