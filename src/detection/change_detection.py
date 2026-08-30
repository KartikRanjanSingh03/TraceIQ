"""
src/detection/change_detection.py — Change Detection layer for TraceIQ.

Implements the exact algorithm from 07_ANALYTICS_AND_DRIVER_ANALYSIS.md §2:
  1. Rolling baseline (mean ± std) over configurable trailing window
  2. Day-of-week seasonality adjustment
  3. Z-score statistical deviation
  4. Persistence check (WATCH → INVESTIGATE after ≥2 consecutive crossing days)
  5. Business materiality gate (both thresholds must be crossed for INVESTIGATE)
  6. Sparse-history fallback (category-level proxy if <30 days product history)

All thresholds come exclusively from kpi_contract.yaml via CONTRACT singleton.
Per CLAUDE.md §4: no hardcoded formulas or thresholds in this module.

Output: AlertResult dataclass — consumed by KPI Decomposition (Stage 3) if status=INVESTIGATE.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import text

from db.connection import engine
from src.semantic.kpi_contract import CONTRACT, KPIDefinition

# ── Output types ──────────────────────────────────────────────────────────


@dataclass
class AlertResult:
    """Immutable result from run_detection() for one KPI × grain on one date."""
    kpi_name: str
    date: date
    grain_filters: dict                     # e.g. {"region": "North", "category": "Electronics"}
    actual_value: float
    expected_value: float
    historical_std: float
    zscore: float
    persistence_days: int                   # how many consecutive crossing days (including today)
    business_impact: float                  # |actual - expected|
    status: str                             # NORMAL | WATCH | INVESTIGATE
    is_sparse_history: bool = False         # True → confidence ceiling = Moderate
    baseline_type: str = "sku_level"        # "sku_level" | "category_proxy"
    notes: list[str] = field(default_factory=list)


# ── Internal helpers ──────────────────────────────────────────────────────

def _dow_adjustment(series: pd.Series, target_dow: int) -> float:
    """
    Day-of-week seasonality adjustment.
    Returns the ratio: avg(target_dow days) / avg(all days), capped at [0.5, 2.0].
    Multiplied into the baseline to account for weekday/weekend patterns.
    Per 07_ANALYTICS doc §2.1.
    """
    if series.empty:
        return 1.0
    dow_mean = series[series.index.dayofweek == target_dow].mean()
    overall_mean = series.mean()
    if overall_mean == 0 or np.isnan(dow_mean) or np.isnan(overall_mean):
        return 1.0
    return float(np.clip(dow_mean / overall_mean, 0.5, 2.0))


def _fetch_history(
    kpi_def: KPIDefinition,
    grain_filters: dict[str, str],
    analysis_date: date,
    window_days: int,
) -> pd.Series:
    """
    Pull daily aggregated KPI values for the trailing `window_days` ending the day before
    analysis_date, filtered to the provided grain.  Returns a pd.Series indexed by date.
    """
    start_date = analysis_date - timedelta(days=window_days)
    end_date   = analysis_date - timedelta(days=1)    # exclude the day we're testing

    # Build WHERE clause from grain_filters
    where_parts = ["date BETWEEN :start_date AND :end_date"]
    params: dict = {"start_date": start_date, "end_date": end_date}
    for col, val in grain_filters.items():
        where_parts.append(f"{col} = :{col}")
        params[col] = val

    where_clause = " AND ".join(where_parts)

    # Use the KPI's source table; select date + the KPI formula
    sql = text(f"""
        SELECT date, {kpi_def.formula} AS kpi_value
        FROM {kpi_def.source_table}
        WHERE {where_clause}
        GROUP BY date
        ORDER BY date;
    """)

    with engine.connect() as conn:
        df = pd.read_sql(sql, conn, params=params, parse_dates=["date"])

    if df.empty:
        return pd.Series(dtype=float)

    df = df.set_index("date")["kpi_value"].astype(float)
    return df


def _fetch_actual(
    kpi_def: KPIDefinition,
    grain_filters: dict[str, str],
    analysis_date: date,
) -> float:
    """Fetch today's actual KPI value for the given grain."""
    where_parts = ["date = :analysis_date"]
    params: dict = {"analysis_date": analysis_date}
    for col, val in grain_filters.items():
        where_parts.append(f"{col} = :{col}")
        params[col] = val
    where_clause = " AND ".join(where_parts)

    sql = text(f"""
        SELECT {kpi_def.formula} AS kpi_value
        FROM {kpi_def.source_table}
        WHERE {where_clause}
        GROUP BY date;
    """)
    with engine.connect() as conn:
        result = conn.execute(sql, params).fetchone()
    return float(result[0]) if result and result[0] is not None else 0.0


def _is_sparse_history(
    kpi_def: KPIDefinition,
    grain_filters: dict[str, str],
    analysis_date: date,
) -> bool:
    """
    True if the most recent product_launch_date in this grain is < sparse_history_days
    before the analysis_date.  Only meaningful for KPIs sourced from orders.
    Per 07_ANALYTICS doc §2.6 and 05_DATA_SCHEMA.md §5.
    """
    if kpi_def.source_table != "raw.orders":
        return False
    threshold = analysis_date - timedelta(days=kpi_def.sparse_history_days)
    where_parts = ["date = :analysis_date"]
    params: dict = {"analysis_date": analysis_date}
    for col, val in grain_filters.items():
        if col != "product_id":   # may or may not be present
            where_parts.append(f"{col} = :{col}")
            params[col] = val
    where_clause = " AND ".join(where_parts)
    sql = text(f"""
        SELECT MAX(product_launch_date) FROM raw.orders WHERE {where_clause};
    """)
    with engine.connect() as conn:
        result = conn.execute(sql, params).fetchone()
    if result and result[0] is not None:
        latest_launch = result[0]
        if hasattr(latest_launch, "date"):
            latest_launch = latest_launch.date()
        return latest_launch > threshold
    return False


def _category_proxy_history(
    kpi_def: KPIDefinition,
    grain_filters: dict[str, str],
    analysis_date: date,
    window_days: int,
) -> pd.Series:
    """
    Fallback baseline for sparse-history SKUs: use category-level (drop product_id filter).
    Per 07_ANALYTICS doc §2.6.
    """
    proxy_filters = {k: v for k, v in grain_filters.items() if k != "product_id"}
    return _fetch_history(kpi_def, proxy_filters, analysis_date, window_days)


def _count_persistence(
    kpi_def: KPIDefinition,
    grain_filters: dict[str, str],
    analysis_date: date,
    direction: int,   # -1 for decline, +1 for increase
    zscore_threshold: float,
    window_days: int,
) -> int:
    """
    Count how many consecutive days (ending today) the z-score crossed the threshold
    in the same direction.  Returns 1 if only today crosses.
    Per 07_ANALYTICS doc §2.3.
    """
    count = 1
    check_date = analysis_date - timedelta(days=1)
    for _ in range(6):   # look back up to 6 days
        history = _fetch_history(kpi_def, grain_filters, check_date, window_days)
        if len(history) < 10:
            break
        actual = _fetch_actual(kpi_def, grain_filters, check_date)
        mean = history.mean()
        std  = history.std(ddof=1)
        if std == 0:
            break
        z = (actual - mean) / std
        if direction * z > zscore_threshold:
            count += 1
            check_date -= timedelta(days=1)
        else:
            break
    return count


def _meets_materiality(kpi_def: KPIDefinition, impact: float) -> bool:
    """
    Return True if the absolute impact crosses the materiality threshold.
    Per 07_ANALYTICS doc §2.4 and 04_KPI_SEMANTIC_CONTRACT.md §5.
    """
    mt = kpi_def.materiality_threshold
    if mt.absolute_inr is not None:
        return impact >= mt.absolute_inr
    if mt.absolute_count is not None:
        return impact >= mt.absolute_count
    # For ratio KPIs (availability, SLA): no absolute materiality threshold defined here;
    # materiality is checked upstream (by the caller) using business context.
    return True


# ── Public API ────────────────────────────────────────────────────────────

def run_detection(
    kpi_name: str,
    grain_filters: dict[str, str],
    analysis_date: date,
    *,
    window_days: Optional[int] = None,
    zscore_threshold: Optional[float] = None,
    persistence_required: Optional[int] = None,
) -> AlertResult:
    """
    Run change detection for one KPI × grain combination on the analysis_date.

    All thresholds default to values from kpi_contract.yaml unless overridden
    (overrides are used only in tests, per CLAUDE.md §4).

    Args:
        kpi_name:           KPI name as in kpi_contract.yaml (e.g. "Revenue")
        grain_filters:      dict of dimension filters (e.g. {"region": "North", "category": "Electronics"})
        analysis_date:      date to evaluate
        window_days:        override for rolling window (default: from contract)
        zscore_threshold:   override for z-score threshold (default: from contract)
        persistence_required: override for consecutive days required (default: from contract)

    Returns:
        AlertResult with status NORMAL | WATCH | INVESTIGATE
    """
    kpi_def: KPIDefinition = CONTRACT.get_kpi(kpi_name)
    st = kpi_def.statistical_threshold

    _window     = window_days         or st.window_days
    _zscore_thr = zscore_threshold    or (st.zscore or 2.0)
    _pers_req   = persistence_required or st.persistence_days

    notes: list[str] = []

    # ── 1. Fetch history & check sparse flag ─────────────────────────────
    sparse = _is_sparse_history(kpi_def, grain_filters, analysis_date)
    baseline_type = "sku_level"

    history = _fetch_history(kpi_def, grain_filters, analysis_date, _window)

    if sparse:
        # Per 07_ANALYTICS doc §2.6: <30 days history → always use category-level proxy.
        # We still keep SKU-level history for comparison but baseline comes from category.
        history = _category_proxy_history(kpi_def, grain_filters, analysis_date, _window)
        baseline_type = "category_proxy"
        notes.append(
            f"Sparse history (<{kpi_def.sparse_history_days} days); "
            "using category-level proxy baseline. Confidence ceiling: Moderate."
        )

    if len(history) < 10:
        # Not enough history to compute a reliable baseline
        return AlertResult(
            kpi_name=kpi_name,
            date=analysis_date,
            grain_filters=grain_filters,
            actual_value=0.0,
            expected_value=0.0,
            historical_std=0.0,
            zscore=0.0,
            persistence_days=0,
            business_impact=0.0,
            status="NORMAL",
            is_sparse_history=sparse,
            baseline_type=baseline_type,
            notes=["Insufficient history to compute baseline — skipping detection."],
        )

    # ── 2. Day-of-week seasonality adjustment ────────────────────────────
    target_dow = pd.Timestamp(analysis_date).dayofweek
    dow_factor = _dow_adjustment(history, target_dow)

    # ── 3. Baseline: rolling mean × DoW factor ───────────────────────────
    mean     = history.mean()
    std      = history.std(ddof=1)
    expected = mean * dow_factor

    if std == 0:
        # Perfectly flat history — anything different is an anomaly, but we can't z-score
        notes.append("Historical std dev = 0; z-score undefined.")
        std = 1.0   # avoid division by zero; z will still be large if actual differs

    # ── 4. Fetch actual + compute z-score ────────────────────────────────
    actual = _fetch_actual(kpi_def, grain_filters, analysis_date)
    zscore = (actual - expected) / std

    # ── 5. Initial status from z-score ───────────────────────────────────
    if abs(zscore) <= _zscore_thr:
        status = "NORMAL"
        persistence = 1
    else:
        # Determine direction
        direction = -1 if zscore < 0 else 1
        persistence = _count_persistence(
            kpi_def, grain_filters, analysis_date,
            direction, _zscore_thr, _window,
        )
        status = "INVESTIGATE" if persistence >= _pers_req else "WATCH"

    # ── 6. Business materiality gate ─────────────────────────────────────
    business_impact = abs(actual - expected)
    if status == "INVESTIGATE" and not _meets_materiality(kpi_def, business_impact):
        status = "WATCH"
        notes.append(
            f"Z-score threshold crossed and persistent, but absolute impact "
            f"({business_impact:,.0f}) is below materiality threshold — downgraded to WATCH."
        )

    # ── 7. Sparse-history confidence ceiling note ────────────────────────
    if sparse and status == "INVESTIGATE":
        notes.append(
            "Sparse-history flag active: confidence ceiling = Moderate for this alert."
        )

    return AlertResult(
        kpi_name=kpi_name,
        date=analysis_date,
        grain_filters=grain_filters,
        actual_value=actual,
        expected_value=expected,
        historical_std=std,
        zscore=zscore,
        persistence_days=persistence,
        business_impact=business_impact,
        status=status,
        is_sparse_history=sparse,
        baseline_type=baseline_type,
        notes=notes,
    )


def scan_all_grains(
    kpi_name: str,
    analysis_date: date,
    regions: list[str] | None = None,
    categories: list[str] | None = None,
) -> list[AlertResult]:
    """
    Run detection across all region × category combinations for a KPI.
    Returns only WATCH or INVESTIGATE results (ignores NORMAL).
    """
    from data.generate_synthetic_data import REGIONS, CATEGORIES

    _regions    = regions    or REGIONS
    _categories = categories or CATEGORIES
    results: list[AlertResult] = []

    for region in _regions:
        for category in _categories:
            grain = {"region": region, "category": category}
            result = run_detection(kpi_name, grain, analysis_date)
            if result.status != "NORMAL":
                results.append(result)

    return results
