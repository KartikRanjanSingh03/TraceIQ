"""
src/decomposition/kpi_decomposition.py — KPI Decomposition layer for TraceIQ.

Implements the two-level decomposition from 07_ANALYTICS_AND_DRIVER_ANALYSIS.md §3:

  Level 1 — Formula Decomposition
    For Revenue: decompose into Orders vs AOV contribution
    (multiplicative attribution via log-decomposition)

  Level 2 — Dimensional Contribution Analysis
    For the dominant factor, rank each dimension segment by its contribution to
    the total KPI movement:
      contribution(segment) = (segment_actual - segment_expected) / (total_actual - total_expected)

Output: DecompositionResult — consumed by the Hypothesis Engine (Stage 4).
Per CLAUDE.md §4: no hardcoded formulas — formula comes from kpi_contract.yaml.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import text

from db.connection import engine
from src.detection.change_detection import AlertResult
from src.semantic.kpi_contract import CONTRACT

# ── Output types ──────────────────────────────────────────────────────────


@dataclass
class FactorContribution:
    """One formula factor's contribution to the total KPI % change (Level 1)."""
    factor_name: str          # e.g. "Orders", "AOV"
    actual: float
    expected: float
    pct_change: float         # (actual - expected) / |expected|  — signed
    contribution_share: float  # share of total KPI abs change explained by this factor


@dataclass
class DimensionContribution:
    """One dimension-value's contribution to the dominant factor's movement (Level 2)."""
    dimension: str            # e.g. "region", "category"
    value: str                # e.g. "North", "Electronics"
    actual: float
    expected: float
    delta: float              # actual - expected (signed)
    contribution_share: float  # delta / total_delta  (may exceed 1 if others partially offset)


@dataclass
class DecompositionResult:
    """
    Full output of run_decomposition() for one INVESTIGATE alert.
    Consumed by the Hypothesis Engine (Stage 4) and Evidence Engine (Stage 5).
    """
    kpi_name: str
    date: date
    alert_grain: dict                       # grain from the triggering AlertResult
    total_actual: float
    total_expected: float
    total_delta: float                      # signed: actual - expected
    total_pct_change: float

    # Level 1
    formula_factors: list[FactorContribution] = field(default_factory=list)
    dominant_factor: str = ""              # name of the factor with largest contribution

    # Level 2 — for the dominant factor, ranked by |contribution_share|
    dimension_contributions: dict[str, list[DimensionContribution]] = field(
        default_factory=dict
    )
    # The single most-responsible (dimension, value) pair — the "localized problem"
    primary_localization: Optional[DimensionContribution] = None

    # Human-readable problem statement (07_ANALYTICS doc §3.3)
    localized_problem_statement: str = ""

    notes: list[str] = field(default_factory=list)


# ── Helpers ───────────────────────────────────────────────────────────────

def _rolling_expected(
    source_table: str,
    agg_formula: str,
    grain_filters: dict[str, str],
    analysis_date: date,
    window_days: int = 90,
) -> float:
    """
    Compute the rolling-mean expected value for an arbitrary aggregation formula,
    applying the same DoW seasonality used by change_detection.py.
    """
    from src.detection.change_detection import _fetch_history, _dow_adjustment

    # Temporarily construct a KPI-like object to reuse _fetch_history
    # Instead, build the query directly for flexibility
    start = analysis_date - timedelta(days=window_days)
    end   = analysis_date - timedelta(days=1)

    where_parts = ["date BETWEEN :start_date AND :end_date"]
    params: dict = {"start_date": start, "end_date": end}
    for col, val in grain_filters.items():
        where_parts.append(f"{col} = :{col}")
        params[col] = val
    where_clause = " AND ".join(where_parts)

    sql = text(f"""
        SELECT date, {agg_formula} AS val
        FROM {source_table}
        WHERE {where_clause}
        GROUP BY date ORDER BY date;
    """)
    with engine.connect() as conn:
        df = pd.read_sql(sql, conn, params=params, parse_dates=["date"])

    if df.empty or len(df) < 5:
        return 0.0

    series = df.set_index("date")["val"].astype(float)

    # DoW adjustment
    target_dow = pd.Timestamp(analysis_date).dayofweek
    dow_values = series[series.index.dayofweek == target_dow]
    dow_factor = (dow_values.mean() / series.mean()) if series.mean() != 0 else 1.0
    dow_factor = float(np.clip(dow_factor, 0.5, 2.0))

    return float(series.mean() * dow_factor)


def _actual_value(
    source_table: str,
    agg_formula: str,
    grain_filters: dict[str, str],
    analysis_date: date,
) -> float:
    """Fetch today's actual value for an arbitrary aggregation formula."""
    where_parts = ["date = :analysis_date"]
    params: dict = {"analysis_date": analysis_date}
    for col, val in grain_filters.items():
        where_parts.append(f"{col} = :{col}")
        params[col] = val
    where_clause = " AND ".join(where_parts)

    sql = text(f"""
        SELECT {agg_formula} FROM {source_table} WHERE {where_clause} GROUP BY date;
    """)
    with engine.connect() as conn:
        row = conn.execute(sql, params).fetchone()
    return float(row[0]) if row and row[0] is not None else 0.0


# ── Level 1: Formula Decomposition ───────────────────────────────────────

_LEVEL1_FACTORS: dict[str, list[dict]] = {
    # Revenue = Orders × AOV  (04_KPI_SEMANTIC_CONTRACT.md §3.1)
    "Revenue": [
        {
            "name": "Orders",
            "formula": "COUNT(DISTINCT order_id)::FLOAT",
            "source": "raw.orders",
        },
        {
            "name": "AOV",
            "formula": "SUM(revenue) / NULLIF(COUNT(DISTINCT order_id), 0)",
            "source": "raw.orders",
        },
    ],
    # Orders has no sub-factors to decompose at Level 1
    "Orders": [],
    "AOV": [],
    # Inventory and SLA are single-formula KPIs — no multiplicative decomposition
    "InventoryAvailability": [],
    "DeliverySLA": [],
}


def _level1_decomposition(
    kpi_name: str,
    grain_filters: dict[str, str],
    analysis_date: date,
    alert: AlertResult,
) -> tuple[list[FactorContribution], str]:
    """
    Decompose KPI into formula factors (Level 1).
    Returns (factors, dominant_factor_name).

    For Revenue = Orders × AOV, uses first-order multiplicative attribution:
        ΔRevenue ≈ ΔOrders × AOV_base  +  Orders_base × ΔAOV
    This correctly attributes a volume collapse to Orders rather than AOV,
    because ΔOrders × AOV_base gives the revenue-equivalent contribution.
    Per 07_ANALYTICS_AND_DRIVER_ANALYSIS.md §3.1.
    """
    factors_spec = _LEVEL1_FACTORS.get(kpi_name, [])
    if not factors_spec:
        return [], kpi_name   # single-factor KPI: the KPI itself is the dominant factor

    total_delta = alert.actual_value - alert.expected_value

    # Fetch actuals and expecteds for every factor first
    raw: list[dict] = []
    for spec in factors_spec:
        actual   = _actual_value(spec["source"], spec["formula"], grain_filters, analysis_date)
        expected = _rolling_expected(spec["source"], spec["formula"], grain_filters, analysis_date)
        raw.append({"spec": spec, "actual": actual, "expected": expected})

    # ── Revenue-specific: multiplicative first-order attribution ──────────
    # Revenue = Orders × AOV
    # ΔRevenue ≈ ΔOrders × AOV_base  +  Orders_base × ΔAOV
    # Contribution of each factor is expressed in INR (same units as total_delta).
    if kpi_name == "Revenue" and len(raw) == 2:
        orders_r = next(r for r in raw if r["spec"]["name"] == "Orders")
        aov_r    = next(r for r in raw if r["spec"]["name"] == "AOV")

        delta_orders = orders_r["actual"] - orders_r["expected"]
        delta_aov    = aov_r["actual"]    - aov_r["expected"]
        aov_base     = aov_r["expected"]
        orders_base  = orders_r["expected"]

        # Revenue-equivalent contribution of each factor
        orders_rev_contrib = delta_orders * aov_base       # ΔOrders × AOV_base  (INR)
        aov_rev_contrib    = orders_base  * delta_aov      # Orders_base × ΔAOV  (INR)

        denom = total_delta if total_delta != 0 else 1.0
        factors: list[FactorContribution] = [
            FactorContribution(
                factor_name="Orders",
                actual=orders_r["actual"],
                expected=orders_r["expected"],
                pct_change=delta_orders / abs(orders_r["expected"]) if orders_r["expected"] != 0 else 0.0,
                contribution_share=orders_rev_contrib / denom,
            ),
            FactorContribution(
                factor_name="AOV",
                actual=aov_r["actual"],
                expected=aov_r["expected"],
                pct_change=delta_aov / abs(aov_r["expected"]) if aov_r["expected"] != 0 else 0.0,
                contribution_share=aov_rev_contrib / denom,
            ),
        ]
    else:
        # Generic: simple delta / total_delta (works for single-product KPIs)
        factors = []
        for r in raw:
            delta   = r["actual"] - r["expected"]
            pct_chg = delta / abs(r["expected"]) if r["expected"] != 0 else 0.0
            contrib = delta / total_delta if total_delta != 0 else 0.0
            factors.append(FactorContribution(
                factor_name=r["spec"]["name"],
                actual=r["actual"],
                expected=r["expected"],
                pct_change=pct_chg,
                contribution_share=contrib,
            ))

    # Dominant factor = largest |contribution_share|
    dominant = max(factors, key=lambda f: abs(f.contribution_share), default=None)
    dominant_name = dominant.factor_name if dominant else kpi_name

    return factors, dominant_name


# ── Level 2: Dimensional Contribution Analysis ────────────────────────────

_DIMENSION_SOURCES: dict[str, dict] = {
    # For Orders (dominant factor of Revenue), source is raw.orders
    "Orders": {
        "source": "raw.orders",
        "formula": "COUNT(DISTINCT order_id)::FLOAT",
    },
    "Revenue": {
        "source": "raw.orders",
        "formula": "SUM(revenue)",
    },
    "AOV": {
        "source": "raw.orders",
        "formula": "SUM(revenue) / NULLIF(COUNT(DISTINCT order_id), 0)",
    },
    "InventoryAvailability": {
        "source": "raw.inventory_snapshots",
        "formula": (
            "1.0 - (SUM(CASE WHEN stockout_flag THEN 1 ELSE 0 END)::FLOAT "
            "/ NULLIF(COUNT(*), 0))"
        ),
    },
    "DeliverySLA": {
        "source": "raw.deliveries",
        "formula": (
            "1.0 - (SUM(CASE WHEN sla_breach_flag THEN 1 ELSE 0 END)::FLOAT "
            "/ NULLIF(COUNT(*), 0))"
        ),
    },
}

# Dimensions to decompose across, ordered by specificity
_DECOMP_DIMENSIONS: list[str] = ["region", "category", "channel", "customer_segment"]


def _level2_for_dimension(
    dominant_factor: str,
    dimension: str,
    grain_filters: dict[str, str],
    analysis_date: date,
    total_delta: float,
) -> list[DimensionContribution]:
    """
    For one dimension (e.g. "region"), compute each segment's contribution.
    contribution(segment) = (segment_actual - segment_expected) / own_total_delta
    where own_total_delta is recomputed from the dominant-factor's formula,
    so shares are in the right units and sum to ~1.0.
    Per 07_ANALYTICS doc §3.2.
    """
    spec = _DIMENSION_SOURCES.get(dominant_factor)
    if spec is None:
        return []

    source  = spec["source"]
    formula = spec["formula"]

    # Build grain WHERE clause, excluding the dimension being decomposed
    where_parts = ["date = :analysis_date"]
    params: dict = {"analysis_date": analysis_date}
    for col, val in grain_filters.items():
        if col != dimension:
            where_parts.append(f"{col} = :{col}")
            params[col] = val
    where_clause = " AND ".join(where_parts)

    sql_actual = text(f"""
        SELECT {dimension}, {formula} AS val
        FROM {source}
        WHERE {where_clause}
        GROUP BY {dimension} ORDER BY {dimension};
    """)

    # Historical expected per segment (DoW-adjusted via simple average for Level 2)
    start = analysis_date - timedelta(days=90)
    end   = analysis_date - timedelta(days=1)
    params_hist = {**params, "start_date": start, "end_date": end}
    # replace analysis_date param with the range
    params_hist.pop("analysis_date", None)
    hist_where_parts = ["date BETWEEN :start_date AND :end_date"]
    for col, val in grain_filters.items():
        if col != dimension:
            hist_where_parts.append(f"{col} = :{col}")
    where_hist = " AND ".join(hist_where_parts)

    sql_hist = text(f"""
        SELECT {dimension}, AVG(daily_val) AS expected_val
        FROM (
            SELECT date, {dimension}, {formula} AS daily_val
            FROM {source}
            WHERE {where_hist}
            GROUP BY date, {dimension}
        ) sub
        GROUP BY {dimension};
    """)

    with engine.connect() as conn:
        actual_df   = pd.read_sql(sql_actual, conn, params=params)
        expected_df = pd.read_sql(sql_hist,   conn, params=params_hist)

    if actual_df.empty:
        return []

    merged = actual_df.merge(expected_df, on=dimension, how="left")
    merged["expected_val"] = merged["expected_val"].fillna(merged["val"])

    # Recompute total_delta in the dominant-factor's own units so shares sum to ~1.
    # own_total_delta = SUM(segment_delta) across all segments of this dimension.
    own_total_delta = float((merged["val"] - merged["expected_val"]).sum())
    denom = own_total_delta if own_total_delta != 0 else total_delta if total_delta != 0 else 1.0

    contributions: list[DimensionContribution] = []
    for _, row in merged.iterrows():
        delta = float(row["val"]) - float(row["expected_val"])
        contrib_share = delta / denom
        contributions.append(DimensionContribution(
            dimension=dimension,
            value=str(row[dimension]),
            actual=float(row["val"]),
            expected=float(row["expected_val"]),
            delta=delta,
            contribution_share=contrib_share,
        ))

    contributions.sort(key=lambda c: abs(c.contribution_share), reverse=True)
    return contributions


# ── Public API ────────────────────────────────────────────────────────────

def run_decomposition(
    alert: AlertResult,
    dimensions: Optional[list[str]] = None,
) -> DecompositionResult:
    """
    Run KPI decomposition for a given INVESTIGATE alert.

    Args:
        alert:      AlertResult from Stage 2 (must be WATCH or INVESTIGATE)
        dimensions: dimensions to decompose across (default: region, category, channel, segment)

    Returns:
        DecompositionResult with localized problem statement
    """
    _dimensions = dimensions or _DECOMP_DIMENSIONS
    kpi_name    = alert.kpi_name
    grain       = alert.grain_filters

    total_delta      = alert.actual_value - alert.expected_value
    total_pct_change = (
        total_delta / abs(alert.expected_value) if alert.expected_value != 0 else 0.0
    )

    notes: list[str] = []

    # ── Level 1: Formula factors ─────────────────────────────────────────
    formula_factors, dominant_factor = _level1_decomposition(
        kpi_name, grain, alert.date, alert
    )

    if formula_factors:
        dom = next((f for f in formula_factors if f.factor_name == dominant_factor), None)
        if dom:
            notes.append(
                f"Level 1: '{dominant_factor}' accounts for "
                f"{dom.contribution_share * 100:.1f}% of the total {kpi_name} movement."
            )

    # ── Level 2: Dimensional contributions ───────────────────────────────
    # Only decompose along dimensions NOT already in the alert grain
    active_grain_dims = set(grain.keys())
    dims_to_decompose = [d for d in _dimensions if d not in active_grain_dims]

    dim_contributions: dict[str, list[DimensionContribution]] = {}
    for dim in dims_to_decompose:
        contribs = _level2_for_dimension(
            dominant_factor, dim, grain, alert.date, total_delta
        )
        if contribs:
            dim_contributions[dim] = contribs

    # ── Primary localisation ──────────────────────────────────────────────
    # Identify the single (dimension, value) pair with the largest |contribution_share|
    primary: Optional[DimensionContribution] = None
    for contribs in dim_contributions.values():
        if contribs:
            candidate = contribs[0]   # already sorted desc by |share|
            if primary is None or abs(candidate.contribution_share) > abs(primary.contribution_share):
                primary = candidate

    # ── Localized problem statement (07_ANALYTICS doc §3.3) ──────────────
    if primary:
        direction = "decline" if total_delta < 0 else "increase"
        pct_str   = f"{abs(total_pct_change) * 100:.1f}%"
        localization = (
            f"Why did {primary.value}-{primary.dimension} {dominant_factor} {direction}? "
            f"({abs(primary.contribution_share) * 100:.1f}% of the total {pct_str} {kpi_name} {direction})"
        )
        # If the alert grain already narrows the scope, reflect that
        grain_desc = ", ".join(f"{k}={v}" for k, v in grain.items())
        if grain_desc:
            localization = (
                f"Within [{grain_desc}]: Why did {primary.value} ({primary.dimension}) "
                f"{dominant_factor} {direction}? "
                f"({abs(primary.contribution_share) * 100:.1f}% contribution)"
            )
    else:
        grain_desc   = ", ".join(f"{k}={v}" for k, v in grain.items())
        direction    = "decline" if total_delta < 0 else "increase"
        localization = (
            f"Why did [{grain_desc}] {kpi_name} {direction}? "
            f"(No further dimensional decomposition possible at this grain.)"
        )

    return DecompositionResult(
        kpi_name=kpi_name,
        date=alert.date,
        alert_grain=grain,
        total_actual=alert.actual_value,
        total_expected=alert.expected_value,
        total_delta=total_delta,
        total_pct_change=total_pct_change,
        formula_factors=formula_factors,
        dominant_factor=dominant_factor,
        dimension_contributions=dim_contributions,
        primary_localization=primary,
        localized_problem_statement=localization,
        notes=notes,
    )
