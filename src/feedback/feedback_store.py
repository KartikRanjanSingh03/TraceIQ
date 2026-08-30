"""
src/feedback/feedback_store.py — Feedback Loop storage for TraceIQ (Stage 13).

Implements 14_FEEDBACK_AND_LEARNING_LOOP.md:
  Stores human decisions (Accept/Reject/Edit/RequestMoreEvidence) against alerts.
  For MVP: SQLite-backed (same DB as the rest of the pipeline) — no extra infra.
  API endpoint /feedback already scaffolded in Stage 11; this module wires the storage.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from db.connection import engine
from sqlalchemy import text


# ── Types ──────────────────────────────────────────────────────────────────

VALID_DECISIONS = {"accept", "reject", "edit", "request_more_evidence"}


@dataclass
class FeedbackEntry:
    alert_id:        str
    decision:        str        # accept | reject | edit | request_more_evidence
    submitted_by:    str        # username
    role:            str        # regional_leader | analyst
    notes:           Optional[str] = None
    hypothesis_override: Optional[str] = None   # if edit: new hypothesis
    submitted_at:    str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def validate(self) -> None:
        if self.decision not in VALID_DECISIONS:
            raise ValueError(f"Invalid decision '{self.decision}'. Must be one of {VALID_DECISIONS}")
        if not self.alert_id:
            raise ValueError("alert_id is required")
        if not self.submitted_by:
            raise ValueError("submitted_by is required")


# ── DDL (creates table if not exists) ────────────────────────────────────

def _ensure_table() -> None:
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS feedback_log (
                id              SERIAL PRIMARY KEY,
                alert_id        TEXT    NOT NULL,
                decision        TEXT    NOT NULL,
                submitted_by    TEXT    NOT NULL,
                role            TEXT    NOT NULL,
                notes           TEXT,
                hypothesis_override TEXT,
                submitted_at    TEXT    NOT NULL
            )
        """))
        conn.commit()


# ── Public API ─────────────────────────────────────────────────────────────

def submit(entry: FeedbackEntry) -> int:
    """
    Persist a feedback entry. Returns the new row ID.
    Per 14_FEEDBACK §3: every state-changing action is logged with user_id, role, timestamp.
    """
    entry.validate()
    _ensure_table()
    with engine.connect() as conn:
        result = conn.execute(text("""
            INSERT INTO feedback_log
              (alert_id, decision, submitted_by, role, notes, hypothesis_override, submitted_at)
            VALUES
              (:alert_id, :decision, :submitted_by, :role, :notes, :hyp_override, :submitted_at)
            RETURNING id
        """), {
            "alert_id":     entry.alert_id,
            "decision":     entry.decision,
            "submitted_by": entry.submitted_by,
            "role":         entry.role,
            "notes":        entry.notes,
            "hyp_override": entry.hypothesis_override,
            "submitted_at": entry.submitted_at,
        })
        row_id = result.fetchone()[0]
        conn.commit()
        return row_id


def get_feedback(alert_id: str) -> list[FeedbackEntry]:
    """Retrieve all feedback for an alert."""
    _ensure_table()
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT alert_id, decision, submitted_by, role, notes, hypothesis_override, submitted_at "
            "FROM feedback_log WHERE alert_id = :alert_id ORDER BY submitted_at DESC"
        ), {"alert_id": alert_id}).fetchall()
    return [
        FeedbackEntry(
            alert_id=r[0], decision=r[1], submitted_by=r[2],
            role=r[3], notes=r[4], hypothesis_override=r[5], submitted_at=r[6]
        )
        for r in rows
    ]


def get_summary() -> dict:
    """
    Aggregate feedback summary for the feedback-loop summary view.
    Per 14_FEEDBACK §4: pattern detection for model improvement.
    """
    _ensure_table()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT decision, COUNT(*) as n
            FROM feedback_log
            GROUP BY decision
            ORDER BY n DESC
        """)).fetchall()
        total = conn.execute(text("SELECT COUNT(*) FROM feedback_log")).scalar()

    counts = {r[0]: r[1] for r in rows}
    accept_rate = (counts.get("accept", 0) / total * 100) if total else 0.0

    return {
        "total_feedback": total,
        "by_decision":    counts,
        "accept_rate_pct": round(accept_rate, 1),
    }
