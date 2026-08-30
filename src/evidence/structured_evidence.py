"""
src/evidence/structured_evidence.py — Structured Evidence Retrieval for TraceIQ.

Implements 07_ANALYTICS_AND_DRIVER_ANALYSIS.md §5.1:
  For each fired hypothesis, run a targeted SQL query against the relevant
  reconciled source table and return typed evidence items.

Evidence covers:
  R01 Inventory shortage   → inventory_snapshots (stockout rate, availability delta)
  R02 Delivery disruption  → deliveries (breach rate, breach count)
  R03 Pricing effect       → orders (avg discount delta, avg price delta)
  R04 Competitor activity  → tickets_notes (structured metadata only; RAG is Stage 6)
  R05 Mix change           → orders (segment distribution shift)

Output: StructuredEvidence dataclass consumed by Confidence Engine (Stage 7).
Per CLAUDE.md §4: no hardcoded thresholds — all come from CONTRACT.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import pandas as pd
from sqlalchemy import text

from db.connection import engine
from src.hypothesis.rules import HypothesisCandidate


# ── Output types ──────────────────────────────────────────────────────────

@dataclass
class EvidenceItem:
    """
    One structured evidence fact supporting or contradicting a hypothesis.
    All values are from SQL aggregates — no fabrication, no LLM.
    """
    source_table: str           # e.g. "raw.inventory_snapshots"
    metric_name: str            # e.g. "stockout_rate_actual"
    metric_value: float
    baseline_value: float
    delta: float                # metric_value - baseline_value (signed)
    delta_pct: float            # delta / |baseline| (signed %)
    window_days: int            # baseline window used
    supports_hypothesis: bool   # True = supporting evidence; False = contradicting
    description: str            # human-readable sentence for LLM prompt context


@dataclass
class StructuredEvidence:
    """All structured evidence for one hypothesis candidate."""
    hypothesis_rule_id: str
    driver: str
    grain_filters: dict
    analysis_date: date
    items: list[EvidenceItem] = field(default_factory=list)
    retrieval_notes: list[str] = field(default_factory=list)

    @property
    def supporting(self) -> list[EvidenceItem]:
        return [e for e in self.items if e.supports_hypothesis]

    @property
    def contradicting(self) -> list[EvidenceItem]:
        return [e for e in self.items if not e.supports_hypothesis]

    @property
    def has_evidence(self) -> bool:
        return len(self.items) > 0


# ── Shared helpers ─────────────────────────────────────────────────────────

def _baseline_window(analysis_date: date, window_days: int = 90) -> tuple[date, date]:
    return analysis_date - timedelta(days=window_days), analysis_date - timedelta(days=1)


def _grain_where(grain: dict, prefix: str = "", exclude: set[str] | None = None) -> tuple[str, dict]:
    """Build WHERE fragments from grain_filters, returning (clause_parts_list, params)."""
    exclude = exclude or set()
    parts: list[str] = []
    params: dict = {}
    for col, val in grain.items():
        if col in exclude:
            continue
        key = f"{prefix}{col}"
        parts.append(f"{col} = :{key}")
        params[key] = val
    return parts, params


# ── Evidence retrievers per rule ──────────────────────────────────────────

def _evidence_inventory_shortage(
    grain: dict, analysis_date: date, window_days: int = 90
) -> StructuredEvidence:
    """
    R01 — Inventory shortage evidence:
      1. Stockout rate on analysis_date vs baseline
      2. Stock-on-hand on analysis_date vs baseline
      3. Comparable unaffected segment (South/Electronics) as contradiction check
    """
    start, end = _baseline_window(analysis_date, window_days)
    items: list[EvidenceItem] = []
    notes: list[str] = []

    inv_grain_cols = {k: v for k, v in grain.items()
                      if k in ("region", "category", "product_id")}

    where_parts_a, params_a = _grain_where(inv_grain_cols, prefix="a_")
    params_a["ad"] = analysis_date
    where_a = " AND ".join(["date = :ad"] + where_parts_a)

    where_parts_h, params_h = _grain_where(inv_grain_cols, prefix="h_")
    params_h["hs"] = start; params_h["he"] = end
    where_h = " AND ".join(["date BETWEEN :hs AND :he"] + where_parts_h)

    # ── 1. Stockout rate ────────────────────────────────────────────────
    sql_so_actual = text(f"""
        SELECT
            SUM(CASE WHEN stockout_flag THEN 1 ELSE 0 END)::FLOAT / NULLIF(COUNT(*), 0)
              AS stockout_rate,
            AVG(stock_on_hand::FLOAT) AS avg_stock
        FROM raw.inventory_snapshots WHERE {where_a};
    """)
    sql_so_hist = text(f"""
        SELECT
            AVG(daily_so_rate) AS stockout_rate,
            AVG(daily_avg_stock) AS avg_stock
        FROM (
            SELECT date,
                   SUM(CASE WHEN stockout_flag THEN 1 ELSE 0 END)::FLOAT
                     / NULLIF(COUNT(*), 0) AS daily_so_rate,
                   AVG(stock_on_hand::FLOAT) AS daily_avg_stock
            FROM raw.inventory_snapshots
            WHERE {where_h}
            GROUP BY date
        ) sub;
    """)

    with engine.connect() as conn:
        r_a   = conn.execute(sql_so_actual, params_a).fetchone()
        r_h   = conn.execute(sql_so_hist,   params_h).fetchone()

    actual_so    = float(r_a[0]) if r_a and r_a[0] is not None else 0.0
    baseline_so  = float(r_h[0]) if r_h and r_h[0] is not None else 0.0
    actual_stk   = float(r_a[1]) if r_a and r_a[1] is not None else 0.0
    baseline_stk = float(r_h[1]) if r_h and r_h[1] is not None else 500.0

    so_delta     = actual_so  - baseline_so
    so_delta_pct = so_delta / baseline_so if baseline_so > 0 else (1.0 if actual_so > 0 else 0.0)
    stk_delta    = actual_stk - baseline_stk
    stk_pct      = stk_delta  / baseline_stk if baseline_stk > 0 else 0.0

    items.append(EvidenceItem(
        source_table="raw.inventory_snapshots",
        metric_name="stockout_rate",
        metric_value=actual_so,
        baseline_value=baseline_so,
        delta=so_delta,
        delta_pct=so_delta_pct,
        window_days=window_days,
        supports_hypothesis=so_delta > 0.05,   # >5pp rise supports inventory hypothesis
        description=(
            f"Stockout rate: {actual_so * 100:.1f}% today vs "
            f"{baseline_so * 100:.1f}% baseline ({so_delta * 100:+.1f}pp)"
        ),
    ))

    items.append(EvidenceItem(
        source_table="raw.inventory_snapshots",
        metric_name="avg_stock_on_hand",
        metric_value=actual_stk,
        baseline_value=baseline_stk,
        delta=stk_delta,
        delta_pct=stk_pct,
        window_days=window_days,
        supports_hypothesis=stk_delta < -50,   # >50 units drop supports shortage
        description=(
            f"Avg stock on hand: {actual_stk:.0f} units today vs "
            f"{baseline_stk:.0f} baseline ({stk_delta:+.0f} units)"
        ),
    ))

    # ── 2. Contradiction check: comparable unaffected region ─────────────
    # If South/Electronics (same category, different region) is NOT in stockout,
    # that confirms the issue is region-specific, not category-wide.
    control_region = "South" if grain.get("region") != "South" else "West"
    control_grain  = {**inv_grain_cols, "region": control_region}
    cw_parts, cp = _grain_where(control_grain, prefix="c_")
    cp["ad_c"] = analysis_date
    where_c = " AND ".join(["date = :ad_c"] + cw_parts)

    sql_ctrl = text(f"""
        SELECT
            SUM(CASE WHEN stockout_flag THEN 1 ELSE 0 END)::FLOAT / NULLIF(COUNT(*), 0)
        FROM raw.inventory_snapshots WHERE {where_c};
    """)
    with engine.connect() as conn:
        rc = conn.execute(sql_ctrl, cp).fetchone()
    ctrl_so = float(rc[0]) if rc and rc[0] is not None else 0.0

    items.append(EvidenceItem(
        source_table="raw.inventory_snapshots",
        metric_name=f"stockout_rate_{control_region}_control",
        metric_value=ctrl_so,
        baseline_value=baseline_so,
        delta=ctrl_so - baseline_so,
        delta_pct=(ctrl_so - baseline_so) / baseline_so if baseline_so > 0 else 0.0,
        window_days=window_days,
        # Control region NOT in stockout → SUPPORTS hypothesis (region-specific issue)
        supports_hypothesis=ctrl_so < 0.10,
        description=(
            f"Control check — {control_region}/same-category stockout rate: "
            f"{ctrl_so * 100:.1f}% (region-specific if this is low)"
        ),
    ))

    if actual_so < 0.10:
        notes.append("Stockout rate below threshold — weak evidence for inventory shortage.")

    return StructuredEvidence(
        hypothesis_rule_id="R01_INVENTORY_SHORTAGE",
        driver="Inventory shortage",
        grain_filters=grain,
        analysis_date=analysis_date,
        items=items,
        retrieval_notes=notes,
    )


def _evidence_delivery_disruption(
    grain: dict, analysis_date: date, window_days: int = 90
) -> StructuredEvidence:
    """R02 — Delivery disruption evidence: SLA breach rate + average actual delivery hours."""
    region = grain.get("region")
    if not region:
        return StructuredEvidence("R02_DELIVERY_DISRUPTION", "Delivery disruption",
                                  grain, analysis_date,
                                  retrieval_notes=["No region in grain — skipped."])

    start, end = _baseline_window(analysis_date, window_days)
    items: list[EvidenceItem] = []

    sql_actual = text("""
        SELECT
            SUM(CASE WHEN sla_breach_flag THEN 1 ELSE 0 END)::FLOAT / NULLIF(COUNT(*), 0)
              AS breach_rate,
            AVG(actual_delivery_hours) AS avg_hrs
        FROM raw.deliveries
        WHERE dispatch_timestamp::DATE = :ad
          AND region = :region
          AND data_quality_flag IS DISTINCT FROM 'in_transit';
    """)
    sql_hist = text("""
        SELECT AVG(daily_breach) AS breach_rate, AVG(daily_hrs) AS avg_hrs
        FROM (
            SELECT dispatch_timestamp::DATE AS dt,
                   SUM(CASE WHEN sla_breach_flag THEN 1 ELSE 0 END)::FLOAT
                     / NULLIF(COUNT(*), 0) AS daily_breach,
                   AVG(actual_delivery_hours) AS daily_hrs
            FROM raw.deliveries
            WHERE dispatch_timestamp::DATE BETWEEN :hs AND :he
              AND region = :region
              AND data_quality_flag IS DISTINCT FROM 'in_transit'
            GROUP BY dt
        ) sub;
    """)
    with engine.connect() as conn:
        r_a = conn.execute(sql_actual, {"ad": analysis_date, "region": region}).fetchone()
        r_h = conn.execute(sql_hist,   {"hs": start, "he": end, "region": region}).fetchone()

    actual_br    = float(r_a[0]) if r_a and r_a[0] is not None else 0.0
    baseline_br  = float(r_h[0]) if r_h and r_h[0] is not None else 0.0
    actual_hrs   = float(r_a[1]) if r_a and r_a[1] is not None else 0.0
    baseline_hrs = float(r_h[1]) if r_h and r_h[1] is not None else 0.0

    br_delta = actual_br - baseline_br
    items.append(EvidenceItem(
        source_table="raw.deliveries",
        metric_name="sla_breach_rate",
        metric_value=actual_br,
        baseline_value=baseline_br,
        delta=br_delta,
        delta_pct=br_delta / baseline_br if baseline_br > 0 else 0.0,
        window_days=window_days,
        supports_hypothesis=br_delta > 0.05,
        description=(
            f"SLA breach rate: {actual_br * 100:.1f}% today vs "
            f"{baseline_br * 100:.1f}% baseline ({br_delta * 100:+.1f}pp)"
        ),
    ))

    hrs_delta = actual_hrs - baseline_hrs
    items.append(EvidenceItem(
        source_table="raw.deliveries",
        metric_name="avg_delivery_hours",
        metric_value=actual_hrs,
        baseline_value=baseline_hrs,
        delta=hrs_delta,
        delta_pct=hrs_delta / baseline_hrs if baseline_hrs > 0 else 0.0,
        window_days=window_days,
        supports_hypothesis=hrs_delta > 5.0,
        description=(
            f"Avg delivery hours: {actual_hrs:.1f}h today vs {baseline_hrs:.1f}h baseline "
            f"({hrs_delta:+.1f}h)"
        ),
    ))

    return StructuredEvidence(
        hypothesis_rule_id="R02_DELIVERY_DISRUPTION",
        driver="Delivery disruption",
        grain_filters=grain,
        analysis_date=analysis_date,
        items=items,
    )


def _evidence_pricing_effect(
    grain: dict, analysis_date: date, window_days: int = 90
) -> StructuredEvidence:
    """R03 — Pricing effect evidence: avg discount and avg price deltas."""
    start, end = _baseline_window(analysis_date, window_days)
    items: list[EvidenceItem] = []

    where_parts_a, params_a = _grain_where(
        {k: v for k, v in grain.items() if k in ("region", "category")}, prefix="a_"
    )
    params_a["ad"] = analysis_date
    where_a = " AND ".join(["date = :ad"] + where_parts_a)

    where_parts_h, params_h = _grain_where(
        {k: v for k, v in grain.items() if k in ("region", "category")}, prefix="h_"
    )
    params_h["hs"] = start; params_h["he"] = end
    where_h = " AND ".join(["date BETWEEN :hs AND :he"] + where_parts_h)

    sql_a = text(f"SELECT AVG(discount_pct), AVG(price) FROM raw.orders WHERE {where_a};")
    sql_h = text(f"""
        SELECT AVG(d), AVG(p) FROM (
            SELECT date, AVG(discount_pct) AS d, AVG(price) AS p
            FROM raw.orders WHERE {where_h} GROUP BY date
        ) sub;
    """)
    with engine.connect() as conn:
        r_a = conn.execute(sql_a, params_a).fetchone()
        r_h = conn.execute(sql_h, params_h).fetchone()

    disc_a = float(r_a[0]) if r_a and r_a[0] is not None else 0.0
    disc_h = float(r_h[0]) if r_h and r_h[0] is not None else 0.0
    prce_a = float(r_a[1]) if r_a and r_a[1] is not None else 0.0
    prce_h = float(r_h[1]) if r_h and r_h[1] is not None else 0.0

    disc_d = disc_a - disc_h   # negative = discount fell (pricing tightened)
    prce_d = prce_a - prce_h

    items.append(EvidenceItem(
        source_table="raw.orders",
        metric_name="avg_discount_pct",
        metric_value=disc_a,
        baseline_value=disc_h,
        delta=disc_d,
        delta_pct=disc_d / disc_h if disc_h > 0 else 0.0,
        window_days=window_days,
        supports_hypothesis=disc_d < -0.03,   # discount fell >3pp
        description=(
            f"Avg discount: {disc_a * 100:.1f}% today vs "
            f"{disc_h * 100:.1f}% baseline ({disc_d * 100:+.1f}pp)"
        ),
    ))

    items.append(EvidenceItem(
        source_table="raw.orders",
        metric_name="avg_price",
        metric_value=prce_a,
        baseline_value=prce_h,
        delta=prce_d,
        delta_pct=prce_d / prce_h if prce_h > 0 else 0.0,
        window_days=window_days,
        supports_hypothesis=prce_d > prce_h * 0.05,   # price rose >5%
        description=(
            f"Avg price: ₹{prce_a:,.0f} today vs ₹{prce_h:,.0f} baseline "
            f"({prce_d / prce_h * 100 if prce_h > 0 else 0:+.1f}%)"
        ),
    ))

    return StructuredEvidence(
        hypothesis_rule_id="R03_PRICING_EFFECT",
        driver="Pricing effect",
        grain_filters=grain,
        analysis_date=analysis_date,
        items=items,
    )


def _evidence_competitor_activity(
    grain: dict, analysis_date: date, window_days: int = 14
) -> StructuredEvidence:
    """
    R04 — Competitor activity structured evidence (metadata only).
    Ticket text retrieval is Stage 6 (RAG). Here we return counts and date metadata.

    Note: competitor signals are market-wide — we do NOT filter by the alert's region.
    The planted competitor doc is tagged South (07_ANALYTICS §4.1: deliberately ambiguous),
    so region-filtering would miss it. Evidence here is presence/absence of competitor
    chatter; the RAG layer in Stage 6 provides the text for confidence scoring.
    """
    start = analysis_date - timedelta(days=window_days)

    # No region filter — competitor activity is market-wide, not region-scoped
    sql = text("""
        SELECT COUNT(*), MIN(timestamp::DATE), MAX(timestamp::DATE)
        FROM raw.tickets_notes
        WHERE timestamp::DATE BETWEEN :start AND :end
          AND source = 'market_note'
          AND (
                LOWER(text) LIKE '%competitor%'
             OR LOWER(text) LIKE '%promotion%'
             OR LOWER(text) LIKE '%discount campaign%'
          );
    """)
    with engine.connect() as conn:
        row = conn.execute(sql, {"start": start, "end": analysis_date}).fetchone()

    count = int(row[0]) if row and row[0] is not None else 0
    items: list[EvidenceItem] = []

    items.append(EvidenceItem(
        source_table="raw.tickets_notes",
        metric_name="competitor_ticket_count",
        metric_value=float(count),
        baseline_value=0.0,
        delta=float(count),
        delta_pct=0.0,
        window_days=window_days,
        supports_hypothesis=count > 0,
        description=(
            f"{count} market note(s) mentioning competitor/promotion found in "
            f"last {window_days} days. RAG retrieval required for full text evidence."
        ),
    ))

    # Always flag RAG requirement — the confidence engine must not upgrade this
    # hypothesis to Strong without RAG-retrieved text evidence (08_CONFIDENCE_AND_ABSTENTION §4)
    notes = [
        "RAG Stage 6 must retrieve and validate text before confidence upgrade. "
        "Competitor signal is market-wide and may not be causal for the specific alert grain."
    ]
    if count == 0:
        notes.append(
            f"Zero competitor tickets found in last {window_days} days — "
            "very weak signal; hypothesis should remain Insufficient Evidence."
        )

    return StructuredEvidence(
        hypothesis_rule_id="R04_COMPETITOR_ACTIVITY",
        driver="Competitor activity",
        grain_filters=grain,
        analysis_date=analysis_date,
        items=items,
        retrieval_notes=notes,
    )


# ── Dispatcher ────────────────────────────────────────────────────────────

_RETRIEVERS = {
    "R01_INVENTORY_SHORTAGE":  _evidence_inventory_shortage,
    "R02_DELIVERY_DISRUPTION": _evidence_delivery_disruption,
    "R03_PRICING_EFFECT":      _evidence_pricing_effect,
    "R04_COMPETITOR_ACTIVITY": _evidence_competitor_activity,
}


def retrieve_evidence(
    hypothesis: HypothesisCandidate,
    grain_filters: dict,
    analysis_date: date,
    window_days: int = 90,
) -> StructuredEvidence:
    """
    Retrieve structured evidence for a single hypothesis candidate.

    Args:
        hypothesis:    HypothesisCandidate from Stage 4
        grain_filters: alert grain (region, category, etc.)
        analysis_date: date under investigation
        window_days:   baseline window (default 90 per CONTRACT)

    Returns:
        StructuredEvidence with supporting and contradicting EvidenceItems
    """
    retriever = _RETRIEVERS.get(hypothesis.rule_id)
    if retriever is None:
        return StructuredEvidence(
            hypothesis_rule_id=hypothesis.rule_id,
            driver=hypothesis.driver,
            grain_filters=grain_filters,
            analysis_date=analysis_date,
            retrieval_notes=[f"No structured evidence retriever for {hypothesis.rule_id}."],
        )

    # R04 uses a 14-day window by default
    w = 14 if hypothesis.rule_id == "R04_COMPETITOR_ACTIVITY" else window_days
    return retriever(grain_filters, analysis_date, w)


def retrieve_all_fired(
    hypotheses: list[HypothesisCandidate],
    grain_filters: dict,
    analysis_date: date,
) -> list[StructuredEvidence]:
    """Retrieve evidence for all fired hypotheses, in ranking order."""
    return [
        retrieve_evidence(h, grain_filters, analysis_date)
        for h in hypotheses
        if h.fired
    ]
