"""
src/semantic/kpi_contract.py — KPI Semantic Contract loader and accessor.

Loads kpi_contract.yaml at import time and provides typed accessors used by
every downstream module (detection, decomposition, evidence, UI).

Per CLAUDE.md §4: no module may hardcode a KPI formula or threshold —
they must call this module instead.
Per 04_KPI_SEMANTIC_CONTRACT.md §8.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Resolve contract path relative to repo root, not this file's location.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONTRACT_PATH = _REPO_ROOT / "kpi_contract.yaml"


def _load_raw() -> dict[str, Any]:
    """Load and return the raw YAML dict from kpi_contract.yaml."""
    with open(_CONTRACT_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


_RAW: dict[str, Any] = _load_raw()


# ---------------------------------------------------------------------------
# Typed dataclasses (light wrappers — keep it simple for MVP)
# ---------------------------------------------------------------------------

@dataclass
class StatisticalThreshold:
    zscore: float | None = None
    window_days: int = 90
    persistence_days: int = 2
    drop_pct_points: float | None = None   # for ratio KPIs (availability, SLA)


@dataclass
class MaterialityThreshold:
    absolute_inr: float | None = None
    absolute_count: int | None = None
    min_revenue_share_pct: float | None = None
    min_shipments_per_day: int | None = None


@dataclass
class AccessRule:
    row_filter: str
    columns: str   # "kpi_only" | "all"


@dataclass
class KPIDefinition:
    name: str
    formula: str
    unit: str
    source_table: str
    grain: list[str]
    drivers: list[str]
    statistical_threshold: StatisticalThreshold
    materiality_threshold: MaterialityThreshold
    sparse_history_days: int
    access: dict[str, AccessRule]
    high_level_formula: str | None = None


@dataclass
class ConfidenceBand:
    min_score: float
    label: str


@dataclass
class Contract:
    domain: str
    currency: str
    analysis_grain: str
    dimensions: list[str]
    kpis: list[KPIDefinition]
    confidence_bands: dict[str, ConfidenceBand]

    def get_kpi(self, name: str) -> KPIDefinition:
        """Return a KPI definition by name (case-insensitive). Raises KeyError if not found."""
        for kpi in self.kpis:
            if kpi.name.lower() == name.lower():
                return kpi
        raise KeyError(f"KPI '{name}' not found in contract. Available: {self.kpi_names}")

    @property
    def kpi_names(self) -> list[str]:
        return [k.name for k in self.kpis]

    def confidence_label(self, score: float) -> str:
        """Map a numeric score (0.0–1.0) to a qualitative label per §6.2 of 07_ANALYTICS doc."""
        for band in ("strong", "moderate", "weak", "insufficient"):
            if score >= self.confidence_bands[band].min_score:
                return self.confidence_bands[band].label
        return "Insufficient"


# ---------------------------------------------------------------------------
# Builder: raw dict → typed Contract
# ---------------------------------------------------------------------------

def _parse_contract(raw: dict[str, Any]) -> Contract:
    kpis: list[KPIDefinition] = []
    for k in raw["kpis"]:
        st_raw = k["statistical_threshold"]
        st = StatisticalThreshold(
            zscore=st_raw.get("zscore"),
            window_days=st_raw.get("window_days", 90),
            persistence_days=st_raw.get("persistence_days", 2),
            drop_pct_points=st_raw.get("drop_pct_points"),
        )
        mt_raw = k["materiality_threshold"]
        mt = MaterialityThreshold(
            absolute_inr=mt_raw.get("absolute_inr"),
            absolute_count=mt_raw.get("absolute_count"),
            min_revenue_share_pct=mt_raw.get("min_revenue_share_pct"),
            min_shipments_per_day=mt_raw.get("min_shipments_per_day"),
        )
        access: dict[str, AccessRule] = {
            role: AccessRule(
                row_filter=rule["row_filter"],
                columns=rule["columns"],
            )
            for role, rule in k["access"].items()
        }
        kpis.append(KPIDefinition(
            name=k["name"],
            formula=k["formula"],
            high_level_formula=k.get("high_level_formula"),
            unit=k["unit"],
            source_table=k["source_table"],
            grain=k["grain"],
            drivers=k["drivers"],
            statistical_threshold=st,
            materiality_threshold=mt,
            sparse_history_days=k.get("sparse_history_days", 30),
            access=access,
        ))

    bands: dict[str, ConfidenceBand] = {
        name: ConfidenceBand(min_score=b["min_score"], label=b["label"])
        for name, b in raw["confidence_bands"].items()
    }

    return Contract(
        domain=raw["domain"],
        currency=raw["currency"],
        analysis_grain=raw["analysis_grain"],
        dimensions=raw["dimensions"],
        kpis=kpis,
        confidence_bands=bands,
    )


# Module-level singleton — import and use directly:
#   from src.semantic.kpi_contract import CONTRACT
#   kpi = CONTRACT.get_kpi("Revenue")
CONTRACT: Contract = _parse_contract(_RAW)
