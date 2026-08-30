"""
src/telemetry/logger.py — Telemetry logger for TraceIQ (Stage 14).

Implements 16_DEPLOYMENT_AND_TELEMETRY.md §3:
  Logs every LLM call with latency, token count, provider, model, estimated cost.
  Consumed by /telemetry/summary (Stage 11) and Section 7 of the UI (Stage 12).
  For MVP: SQLite-backed, same DB as pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from db.connection import engine
from src.llm.llm_client import LLMResponse
from sqlalchemy import text

# Cost estimates (per 1K tokens) — Gemini free-tier / mock approximation
_COST_PER_1K = {
    "mock":   0.0,
    "gemini": 0.000075,   # ~Gemini Flash
    "openai": 0.00015,    # ~GPT-4o-mini
}


# ── DDL ────────────────────────────────────────────────────────────────────

def _ensure_table() -> None:
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS telemetry_log (
                id              SERIAL PRIMARY KEY,
                alert_id        TEXT,
                persona         TEXT,
                provider        TEXT,
                model           TEXT,
                prompt_tokens   INTEGER,
                completion_tokens INTEGER,
                latency_ms      REAL,
                estimated_cost  REAL,
                cached          INTEGER,
                logged_at       TEXT NOT NULL
            )
        """))
        conn.commit()


# ── Public API ─────────────────────────────────────────────────────────────

def log_llm_call(
    llm_response: LLMResponse,
    *,
    alert_id: Optional[str] = None,
    persona:  Optional[str] = None,
) -> None:
    """
    Log one LLM call to telemetry_log.
    Called by the narrative layer (Stage 10) after each LLM call.
    Per 09_LLM_ARCHITECTURE §9 + 16_DEPLOYMENT §3.
    """
    _ensure_table()
    total_k = llm_response.total_tokens / 1000
    cost    = total_k * _COST_PER_1K.get(llm_response.provider, 0.0)

    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO telemetry_log
              (alert_id, persona, provider, model, prompt_tokens, completion_tokens,
               latency_ms, estimated_cost, cached, logged_at)
            VALUES
              (:alert_id, :persona, :provider, :model, :pt, :ct, :lat, :cost, :cached, :logged_at)
        """), {
            "alert_id": alert_id,
            "persona":  persona,
            "provider": llm_response.provider,
            "model":    llm_response.model,
            "pt":       llm_response.prompt_tokens,
            "ct":       llm_response.completion_tokens,
            "lat":      llm_response.latency_ms,
            "cost":     cost,
            "cached":   int(llm_response.cached),
            "logged_at": datetime.now(timezone.utc).isoformat(),
        })
        conn.commit()


def get_summary() -> dict:
    """
    Aggregate telemetry summary for /telemetry/summary endpoint and UI Section 7.
    Per 16_DEPLOYMENT §3.
    """
    _ensure_table()
    with engine.connect() as conn:
        total_calls = conn.execute(
            text("SELECT COUNT(*) FROM telemetry_log")
        ).scalar() or 0
        cached_calls = conn.execute(
            text("SELECT COUNT(*) FROM telemetry_log WHERE cached = 1")
        ).scalar() or 0
        avg_latency = conn.execute(
            text("SELECT AVG(latency_ms) FROM telemetry_log WHERE cached = 0")
        ).scalar() or 0.0
        total_tokens = conn.execute(
            text("SELECT SUM(prompt_tokens + completion_tokens) FROM telemetry_log")
        ).scalar() or 0
        total_cost = conn.execute(
            text("SELECT SUM(estimated_cost) FROM telemetry_log")
        ).scalar() or 0.0

    return {
        "total_llm_calls":   total_calls,
        "cached_calls":      cached_calls,
        "avg_latency_ms":    round(avg_latency, 2),
        "total_tokens":      total_tokens,
        "estimated_cost_usd": round(total_cost, 6),
    }
