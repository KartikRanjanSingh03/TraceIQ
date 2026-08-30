"""
src/hypothesis/rules.py — Rule-Based Hypothesis Engine for TraceIQ.

Implements the deterministic if/else rules from 07_ANALYTICS_AND_DRIVER_ANALYSIS.md §4.1.
Each rule checks co-occurring signals in the structured data and produces a ranked list
of candidate hypotheses.

These are the primary, authoritative hypotheses. LLM suggestions (Stage 4b) are secondary
and go through the same evidence validation (07_ANALYTICS doc §4.2).

Per CLAUDE.md §4: all thresholds in this file come from kpi_contract.yaml via CONTRACT.
No hardcoded thresholds.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import pandas as pd
from sqlalchemy import text

from db.connection import engine
from src.decomposition.kpi_decomposition import DecompositionResult
from src.semantic.kpi_contract import CONTRACT

# ── Output type ───────────────────────────────────────────────────────────


@dataclass
class HypothesisCandidate:
    """
    One candidate hypothesis produced by the rules engine.
    All candidates go through the Evidence + Confidence engine (Stages 5-6) before ranking.
    """
    driver: str                     # human-readable driver name
    rule_id: str                    # unique rule identifier (for traceability)
    fired: bool                     # True if the rule's conditions are met
    signal_strength: float          # 0.0–1.0, based on signal magnitude vs baseline
    generated_by: str = "rule"      # always "rule" in this module; "llm_suggested" set by Stage 4b
    supporting_signals: list[str] = field(default_factory=list)  # text descriptions of signals
    notes: list[str] = field(default_factory=list)


# ── Signal queries (each returns a float magnitude 0+ or a bool) ──────────

def _stockout_rate_change(
    grain_filters: dict[str, str],
    analysis_date: date,
    window_days: int = 90,
) -> tuple[float, float]:
    """
    Returns (actual_stockout_rate, baseline_stockout_rate) for the grain.
    actual   = stockout_rate on analysis_date
    baseline = average stockout_rate over trailing window
    """
    start = analysis_date - timedelta(days=window_days)

    where_parts_actual = ["date = :analysis_date"]
    where_parts_hist   = ["date BETWEEN :start_date AND :end_date"]
    params_actual: dict = {"analysis_date": analysis_date}
    params_hist:   dict = {"start_date": start, "end_date": analysis_date - timedelta(days=1)}

    for col, val in grain_filters.items():
        if col in ("region", "category", "product_id"):
            where_parts_actual.append(f"{col} = :{col}_a")
            where_parts_hist.append(f"{col} = :{col}_h")
            params_actual[f"{col}_a"] = val
            params_hist[f"{col}_h"]   = val

    sql_actual = text(f"""
        SELECT
            SUM(CASE WHEN stockout_flag THEN 1 ELSE 0 END)::FLOAT / NULLIF(COUNT(*), 0)
        FROM raw.inventory_snapshots
        WHERE {" AND ".join(where_parts_actual)};
    """)
    sql_hist = text(f"""
        SELECT AVG(daily_rate) FROM (
            SELECT date,
                   SUM(CASE WHEN stockout_flag THEN 1 ELSE 0 END)::FLOAT
                   / NULLIF(COUNT(*), 0) AS daily_rate
            FROM raw.inventory_snapshots
            WHERE {" AND ".join(where_parts_hist)}
            GROUP BY date
        ) sub;
    """)
    with engine.connect() as conn:
        actual_row = conn.execute(sql_actual, params_actual).fetchone()
        hist_row   = conn.execute(sql_hist,   params_hist).fetchone()

    actual   = float(actual_row[0]) if actual_row and actual_row[0] is not None else 0.0
    baseline = float(hist_row[0])   if hist_row   and hist_row[0]   is not None else 0.0
    return actual, baseline


def _sla_breach_rate_change(
    grain_filters: dict[str, str],
    analysis_date: date,
    window_days: int = 90,
) -> tuple[float, float]:
    """Returns (actual_breach_rate, baseline_breach_rate) for the grain (by region)."""
    region = grain_filters.get("region")
    if not region:
        return 0.0, 0.0

    start = analysis_date - timedelta(days=window_days)

    sql_actual = text("""
        SELECT
            SUM(CASE WHEN sla_breach_flag THEN 1 ELSE 0 END)::FLOAT / NULLIF(COUNT(*), 0)
        FROM raw.deliveries
        WHERE dispatch_timestamp::DATE = :analysis_date
          AND region = :region
          AND data_quality_flag IS DISTINCT FROM 'in_transit';
    """)
    sql_hist = text("""
        SELECT AVG(daily_rate) FROM (
            SELECT dispatch_timestamp::DATE AS dt,
                   SUM(CASE WHEN sla_breach_flag THEN 1 ELSE 0 END)::FLOAT
                   / NULLIF(COUNT(*), 0) AS daily_rate
            FROM raw.deliveries
            WHERE dispatch_timestamp::DATE BETWEEN :start_date AND :end_date
              AND region = :region
              AND data_quality_flag IS DISTINCT FROM 'in_transit'
            GROUP BY dt
        ) sub;
    """)
    with engine.connect() as conn:
        actual_row = conn.execute(sql_actual,
            {"analysis_date": analysis_date, "region": region}).fetchone()
        hist_row = conn.execute(sql_hist,
            {"start_date": start, "end_date": analysis_date - timedelta(days=1),
             "region": region}).fetchone()

    actual   = float(actual_row[0]) if actual_row and actual_row[0] is not None else 0.0
    baseline = float(hist_row[0])   if hist_row   and hist_row[0]   is not None else 0.0
    return actual, baseline


def _avg_discount_change(
    grain_filters: dict[str, str],
    analysis_date: date,
    window_days: int = 90,
) -> tuple[float, float]:
    """Returns (actual_avg_discount, baseline_avg_discount) for the grain."""
    start = analysis_date - timedelta(days=window_days)

    where_actual = ["date = :analysis_date"]
    where_hist   = ["date BETWEEN :start_date AND :end_date"]
    params_a: dict = {"analysis_date": analysis_date}
    params_h: dict = {"start_date": start, "end_date": analysis_date - timedelta(days=1)}

    for col, val in grain_filters.items():
        if col in ("region", "category"):
            where_actual.append(f"{col} = :{col}_a")
            where_hist.append(f"{col} = :{col}_h")
            params_a[f"{col}_a"] = val
            params_h[f"{col}_h"] = val

    sql_actual = text(f"""
        SELECT AVG(discount_pct) FROM raw.orders
        WHERE {" AND ".join(where_actual)};
    """)
    sql_hist = text(f"""
        SELECT AVG(daily_disc) FROM (
            SELECT date, AVG(discount_pct) AS daily_disc
            FROM raw.orders
            WHERE {" AND ".join(where_hist)}
            GROUP BY date
        ) sub;
    """)
    with engine.connect() as conn:
        ar = conn.execute(sql_actual, params_a).fetchone()
        hr = conn.execute(sql_hist,   params_h).fetchone()

    actual   = float(ar[0]) if ar and ar[0] is not None else 0.0
    baseline = float(hr[0]) if hr and hr[0] is not None else 0.0
    return actual, baseline


def _segment_mix_shift(
    grain_filters: dict[str, str],
    analysis_date: date,
    window_days: int = 90,
) -> float:
    """
    Compute Total Variation Distance of customer_segment distribution
    between analysis_date and baseline period.  Returns 0.0–1.0.
    """
    start = analysis_date - timedelta(days=window_days)

    where_a = ["date = :analysis_date"]
    where_h = ["date BETWEEN :start_date AND :end_date"]
    pa: dict = {"analysis_date": analysis_date}
    ph: dict = {"start_date": start, "end_date": analysis_date - timedelta(days=1)}

    for col, val in grain_filters.items():
        if col in ("region", "category"):
            where_a.append(f"{col} = :{col}_a"); pa[f"{col}_a"] = val
            where_h.append(f"{col} = :{col}_h"); ph[f"{col}_h"] = val

    sql_a = text(f"""
        SELECT customer_segment, COUNT(DISTINCT order_id)::FLOAT AS cnt
        FROM raw.orders WHERE {" AND ".join(where_a)}
        GROUP BY customer_segment;
    """)
    sql_h = text(f"""
        SELECT customer_segment, COUNT(DISTINCT order_id)::FLOAT AS cnt
        FROM raw.orders WHERE {" AND ".join(where_h)}
        GROUP BY customer_segment;
    """)
    with engine.connect() as conn:
        df_a = pd.read_sql(sql_a, conn, params=pa)
        df_h = pd.read_sql(sql_h, conn, params=ph)

    if df_a.empty or df_h.empty:
        return 0.0

    tot_a = df_a["cnt"].sum(); tot_h = df_h["cnt"].sum()
    if tot_a == 0 or tot_h == 0:
        return 0.0

    df_a = df_a.set_index("customer_segment")["cnt"] / tot_a
    df_h = df_h.set_index("customer_segment")["cnt"] / tot_h
    idx  = df_a.index.union(df_h.index)
    dist = 0.5 * (df_a.reindex(idx, fill_value=0) - df_h.reindex(idx, fill_value=0)).abs().sum()
    return float(dist)


def _competitor_ticket_present(
    grain_filters: dict[str, str],
    analysis_date: date,
    window_days: int = 14,
) -> bool:
    """
    True if a market_note ticket mentioning competitor activity exists
    in the tickets_notes table within the alert window.
    Tag: text contains 'competitor' or 'promotion' or 'discount campaign'.
    """
    start = analysis_date - timedelta(days=window_days)
    region = grain_filters.get("region")

    sql = text("""
        SELECT COUNT(*) FROM raw.tickets_notes
        WHERE timestamp::DATE BETWEEN :start AND :end
          AND source = 'market_note'
          AND (
                LOWER(text) LIKE '%competitor%'
             OR LOWER(text) LIKE '%promotion%'
             OR LOWER(text) LIKE '%discount campaign%'
          )
          AND (:region IS NULL OR region = :region OR region IS NULL);
    """)
    with engine.connect() as conn:
        row = conn.execute(sql, {
            "start": start, "end": analysis_date, "region": region
        }).fetchone()
    return bool(row and row[0] > 0)


# ── Thresholds from CONTRACT ───────────────────────────────────────────────

_STOCKOUT_SIGNAL_THRESHOLD = 0.10   # >10pp absolute rise in stockout rate
_SLA_SIGNAL_THRESHOLD      = 0.05   # >5pp absolute rise in breach rate
_DISCOUNT_SIGNAL_THRESHOLD = 0.03   # >3pp absolute fall in discount → pricing effect
_MIX_SHIFT_THRESHOLD       = 0.05   # Total Variation Distance >5%


# ── Rule implementations ──────────────────────────────────────────────────

def _rule_inventory_shortage(
    grain: dict, analysis_date: date, orders_declined: bool
) -> HypothesisCandidate:
    """
    07_ANALYTICS §4.1 Rule 1: Orders↓ AND Stockouts↑ → Inventory shortage
    """
    actual_so, baseline_so = _stockout_rate_change(grain, analysis_date)
    so_rise = actual_so - baseline_so
    fired   = orders_declined and so_rise > _STOCKOUT_SIGNAL_THRESHOLD
    strength = min(1.0, so_rise / 0.5) if fired else 0.0   # normalise: 50pp rise = max strength

    signals = []
    if fired:
        signals.append(
            f"Stockout rate rose {so_rise * 100:.1f}pp "
            f"(actual {actual_so * 100:.1f}% vs baseline {baseline_so * 100:.1f}%)"
        )
    return HypothesisCandidate(
        driver="Inventory shortage",
        rule_id="R01_INVENTORY_SHORTAGE",
        fired=fired,
        signal_strength=strength,
        supporting_signals=signals,
    )


def _rule_delivery_disruption(
    grain: dict, analysis_date: date, orders_declined: bool
) -> HypothesisCandidate:
    """
    07_ANALYTICS §4.1 Rule 2: Orders↓ AND SLA_breach↑ → Delivery disruption
    """
    actual_breach, baseline_breach = _sla_breach_rate_change(grain, analysis_date)
    breach_rise = actual_breach - baseline_breach
    fired   = orders_declined and breach_rise > _SLA_SIGNAL_THRESHOLD
    strength = min(1.0, breach_rise / 0.3) if fired else 0.0

    signals = []
    if fired:
        signals.append(
            f"SLA breach rate rose {breach_rise * 100:.1f}pp "
            f"(actual {actual_breach * 100:.1f}% vs baseline {baseline_breach * 100:.1f}%)"
        )
    return HypothesisCandidate(
        driver="Delivery disruption",
        rule_id="R02_DELIVERY_DISRUPTION",
        fired=fired,
        signal_strength=strength,
        supporting_signals=signals,
    )


def _rule_pricing_effect(
    grain: dict, analysis_date: date, orders_declined: bool
) -> HypothesisCandidate:
    """
    07_ANALYTICS §4.1 Rule 3: Orders↓ AND discount↓ (or price↑) → Pricing effect
    A fall in average discount means customers are paying more → demand suppressed.
    """
    actual_disc, baseline_disc = _avg_discount_change(grain, analysis_date)
    disc_fall = baseline_disc - actual_disc   # positive = discount fell (pricing got tighter)
    fired   = orders_declined and disc_fall > _DISCOUNT_SIGNAL_THRESHOLD
    strength = min(1.0, disc_fall / 0.15) if fired else 0.0

    signals = []
    if fired:
        signals.append(
            f"Average discount fell {disc_fall * 100:.1f}pp "
            f"(actual {actual_disc * 100:.1f}% vs baseline {baseline_disc * 100:.1f}%)"
        )
    return HypothesisCandidate(
        driver="Pricing effect",
        rule_id="R03_PRICING_EFFECT",
        fired=fired,
        signal_strength=strength,
        supporting_signals=signals,
    )


def _rule_competitor_activity(
    grain: dict, analysis_date: date, orders_declined: bool
) -> HypothesisCandidate:
    """
    07_ANALYTICS §4.1 Rule 5: Orders↓ AND external ticket mentions competitor
    → Competitor activity (candidate only — confirmed by Evidence Engine)
    """
    ticket_found = _competitor_ticket_present(grain, analysis_date)
    fired   = orders_declined and ticket_found
    strength = 0.30 if fired else 0.0   # weak signal by design — needs RAG evidence

    signals = []
    if fired:
        signals.append(
            "Market note(s) mentioning competitor activity found in tickets_notes "
            "within the last 14 days. Requires RAG evidence validation."
        )
    return HypothesisCandidate(
        driver="Competitor activity",
        rule_id="R04_COMPETITOR_ACTIVITY",
        fired=fired,
        signal_strength=strength,
        supporting_signals=signals,
        notes=["Candidate only — must be validated by Evidence Engine (Stage 5)."],
    )


def _rule_segment_mix_change(
    grain: dict, analysis_date: date, orders_declined: bool
) -> HypothesisCandidate:
    """
    07_ANALYTICS §4.1 Rule 6: Orders↓ AND customer_segment mix shifted
    → Customer/product mix change
    """
    tvd = _segment_mix_shift(grain, analysis_date)
    fired   = orders_declined and tvd > _MIX_SHIFT_THRESHOLD
    strength = min(1.0, tvd / 0.20) if fired else 0.0

    signals = []
    if fired:
        signals.append(
            f"Customer segment mix shift detected: Total Variation Distance = {tvd:.3f} "
            f"(threshold: {_MIX_SHIFT_THRESHOLD})"
        )
    return HypothesisCandidate(
        driver="Customer/product mix change",
        rule_id="R05_MIX_CHANGE",
        fired=fired,
        signal_strength=strength,
        supporting_signals=signals,
    )


def _rule_demand_decline(
    grain: dict, analysis_date: date, orders_declined: bool,
    other_hypotheses: list[HypothesisCandidate],
) -> HypothesisCandidate:
    """
    07_ANALYTICS §4.1 Rule 4: Orders↓ AND no supply/pricing/delivery signal
    → Demand decline (residual — fires when no other rule fires)
    """
    any_fired = any(h.fired for h in other_hypotheses)
    fired   = orders_declined and not any_fired
    strength = 0.40 if fired else 0.0   # moderate — residual hypothesis

    signals = []
    if fired:
        signals.append(
            "No inventory, delivery, pricing, competitor, or mix signal detected. "
            "Order decline attributed to unexplained demand reduction."
        )
    return HypothesisCandidate(
        driver="Demand decline",
        rule_id="R06_DEMAND_DECLINE",
        fired=fired,
        signal_strength=strength,
        supporting_signals=signals,
        notes=["Residual hypothesis — fires only when no other rule fires."],
    )


# ── Public API ────────────────────────────────────────────────────────────

def generate_hypotheses(
    decomp: DecompositionResult,
    *,
    orders_pct_change_threshold: float = -0.05,   # >5% orders decline → "orders declined"
) -> list[HypothesisCandidate]:
    """
    Generate ranked candidate hypotheses from the decomposition result.

    Args:
        decomp:                      DecompositionResult from Stage 3
        orders_pct_change_threshold: minimum % orders decline to treat as "orders declined"
                                     (default -0.05; overridable in tests per CLAUDE.md §4)

    Returns:
        List of HypothesisCandidate sorted by signal_strength descending.
        Includes both fired and unfired candidates (consumers filter on .fired).
    """
    grain          = decomp.alert_grain
    analysis_date  = decomp.date

    # Determine whether Orders declined (the prerequisite for all rules)
    orders_factor = next(
        (f for f in decomp.formula_factors if f.factor_name == "Orders"), None
    )
    if orders_factor is not None:
        orders_declined = orders_factor.pct_change < orders_pct_change_threshold
    else:
        # Non-Revenue KPI: use total KPI pct_change as proxy
        orders_declined = decomp.total_pct_change < orders_pct_change_threshold

    # Fire each rule
    r01 = _rule_inventory_shortage(grain, analysis_date, orders_declined)
    r02 = _rule_delivery_disruption(grain, analysis_date, orders_declined)
    r03 = _rule_pricing_effect(grain, analysis_date, orders_declined)
    r04 = _rule_competitor_activity(grain, analysis_date, orders_declined)
    r05 = _rule_segment_mix_change(grain, analysis_date, orders_declined)
    r06 = _rule_demand_decline(grain, analysis_date, orders_declined,
                               [r01, r02, r03, r04, r05])

    all_candidates = [r01, r02, r03, r04, r05, r06]

    # Sort fired hypotheses first, then by signal_strength desc
    all_candidates.sort(key=lambda h: (not h.fired, -h.signal_strength))
    return all_candidates
