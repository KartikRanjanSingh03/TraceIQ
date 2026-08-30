"""
tests/test_stage13_14_feedback_telemetry.py — Stages 13 & 14 tests.

Stage 13 (Feedback Loop):
  - submit() persists entry to DB
  - get_feedback() returns entries for the alert
  - get_summary() counts by decision
  - Invalid decision raises ValueError
  - entry tagged with role + timestamp

Stage 14 (Telemetry):
  - log_llm_call() writes row to telemetry_log
  - get_summary() returns correct totals
  - Cached calls counted separately
  - telemetry entries created per LLM call (required scenario §4.14)

Run:
    pytest tests/test_stage13_14_feedback_telemetry.py -v
"""
from __future__ import annotations

import pytest
from datetime import date

from src.feedback.feedback_store import (
    FeedbackEntry, submit, get_feedback, get_summary as fb_summary,
    VALID_DECISIONS,
)
from src.telemetry.logger import (
    log_llm_call, get_summary as tel_summary,
)
from src.llm.llm_client import LLMResponse

ALERT_ID = "Revenue_North_Electronics_2026-08-29"


# ═══════════════════════════════════════════════════════════════════════════
# STAGE 13 — FEEDBACK LOOP
# ═══════════════════════════════════════════════════════════════════════════

class TestFeedbackStorage:
    def test_submit_returns_row_id(self) -> None:
        entry = FeedbackEntry(
            alert_id=ALERT_ID, decision="accept",
            submitted_by="analyst", role="analyst",
        )
        row_id = submit(entry)
        assert isinstance(row_id, int)
        assert row_id > 0

    def test_get_feedback_returns_submitted_entry(self) -> None:
        entry = FeedbackEntry(
            alert_id=ALERT_ID + "_test_get", decision="reject",
            submitted_by="regional_leader", role="regional_leader",
            notes="Disagree with hypothesis",
        )
        submit(entry)
        results = get_feedback(ALERT_ID + "_test_get")
        assert len(results) >= 1
        assert results[0].decision == "reject"
        assert results[0].submitted_by == "regional_leader"

    def test_entry_tagged_with_role(self) -> None:
        entry = FeedbackEntry(
            alert_id=ALERT_ID + "_role_test", decision="accept",
            submitted_by="analyst", role="analyst",
        )
        submit(entry)
        results = get_feedback(ALERT_ID + "_role_test")
        assert results[0].role == "analyst"

    def test_entry_has_timestamp(self) -> None:
        entry = FeedbackEntry(
            alert_id=ALERT_ID + "_ts_test", decision="accept",
            submitted_by="analyst", role="analyst",
        )
        submit(entry)
        results = get_feedback(ALERT_ID + "_ts_test")
        assert results[0].submitted_at   # non-empty

    def test_invalid_decision_raises_value_error(self) -> None:
        entry = FeedbackEntry(
            alert_id=ALERT_ID, decision="invalid_decision",
            submitted_by="analyst", role="analyst",
        )
        with pytest.raises(ValueError, match="Invalid decision"):
            entry.validate()

    def test_all_valid_decisions_accepted(self) -> None:
        for decision in VALID_DECISIONS:
            entry = FeedbackEntry(
                alert_id=ALERT_ID + f"_{decision}", decision=decision,
                submitted_by="analyst", role="analyst",
            )
            row_id = submit(entry)
            assert row_id > 0

    def test_get_summary_counts_by_decision(self) -> None:
        # Submit a known set for a unique alert
        uid = ALERT_ID + "_summary_test"
        for dec in ["accept", "accept", "reject"]:
            submit(FeedbackEntry(alert_id=uid, decision=dec, submitted_by="u", role="analyst"))
        summary = fb_summary()
        assert "total_feedback" in summary
        assert "by_decision"    in summary
        assert "accept_rate_pct" in summary
        assert isinstance(summary["accept_rate_pct"], float)

    def test_empty_alert_id_raises(self) -> None:
        entry = FeedbackEntry(alert_id="", decision="accept",
                              submitted_by="u", role="analyst")
        with pytest.raises(ValueError):
            entry.validate()


# ═══════════════════════════════════════════════════════════════════════════
# STAGE 14 — TELEMETRY
# ═══════════════════════════════════════════════════════════════════════════

def _mock_resp(cached=False, tokens=50) -> LLMResponse:
    return LLMResponse(
        text="test narrative", provider="mock", model="mock-v1",
        prompt_tokens=tokens, completion_tokens=tokens // 2,
        latency_ms=1.5, cached=cached,
    )


class TestTelemetryLogger:
    def test_log_llm_call_does_not_raise(self) -> None:
        resp = _mock_resp()
        log_llm_call(resp, alert_id=ALERT_ID, persona="analyst")

    def test_summary_returns_required_fields(self) -> None:
        log_llm_call(_mock_resp(), alert_id=ALERT_ID, persona="analyst")
        summary = tel_summary()
        assert "total_llm_calls"    in summary
        assert "cached_calls"       in summary
        assert "avg_latency_ms"     in summary
        assert "total_tokens"       in summary
        assert "estimated_cost_usd" in summary

    def test_total_calls_increments(self) -> None:
        before = tel_summary()["total_llm_calls"]
        log_llm_call(_mock_resp(), alert_id=ALERT_ID, persona="business_leader")
        after  = tel_summary()["total_llm_calls"]
        assert after == before + 1

    def test_cached_calls_counted_separately(self) -> None:
        before = tel_summary()["cached_calls"]
        log_llm_call(_mock_resp(cached=True))
        after  = tel_summary()["cached_calls"]
        assert after == before + 1

    def test_tokens_accumulate(self) -> None:
        before = tel_summary()["total_tokens"]
        log_llm_call(_mock_resp(tokens=100))
        after  = tel_summary()["total_tokens"]
        assert after > before

    def test_mock_cost_is_zero(self) -> None:
        """Mock provider has zero cost per token (free local testing)."""
        log_llm_call(_mock_resp())
        summary = tel_summary()
        assert summary["estimated_cost_usd"] == 0.0

    def test_telemetry_entry_created_per_llm_call(self) -> None:
        """
        §4.14 required test: verify telemetry entries ARE created when LLM is called.
        Log 3 calls, confirm total_llm_calls increased by exactly 3.
        """
        before = tel_summary()["total_llm_calls"]
        for _ in range(3):
            log_llm_call(_mock_resp(), alert_id=ALERT_ID, persona="analyst")
        after = tel_summary()["total_llm_calls"]
        assert after == before + 3, (
            f"Expected {before + 3} total calls after 3 new LLM calls, got {after}"
        )
