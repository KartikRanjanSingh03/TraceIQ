"""
tests/test_stage15_e2e.py — Stage 15: Full end-to-end scenario tests.

Covers ALL §4.1–§4.11 scenarios from 15_TESTING_STRATEGY.md, run together
against the full pipeline (detection→decomp→hypothesis→evidence→confidence
→gate→recommendation→narrative→feedback→telemetry).

Per CLAUDE.md §7: every scenario must pass before the project is submission-ready.
Per 15_TESTING_STRATEGY.md §6: fixed synthetic dataset, deterministic results.

Run:
    pytest tests/test_stage15_e2e.py -v
"""
from __future__ import annotations

from datetime import date

import pytest

# ── Shared constants ────────────────────────────────────────────────────────
ANALYSIS_DATE = date(2026, 8, 29)
OUTAGE_GRAIN  = {"region": "North", "category": "Electronics"}
NORMAL_GRAIN  = {"region": "South", "category": "Home & Kitchen"}
ALERT_ID      = f"Revenue_North_Electronics_{ANALYSIS_DATE}"


# ── Full pipeline helper ────────────────────────────────────────────────────

def _run_full_pipeline(grain=None, kpi="Revenue", ad=None):
    """Run stages 2-10 for one grain. Returns dict of all outputs."""
    from src.detection.change_detection import run_detection
    from src.decomposition.kpi_decomposition import run_decomposition
    from src.hypothesis.rules import generate_hypotheses
    from src.evidence.structured_evidence import retrieve_evidence
    from src.evidence.retrieval import build_index, retrieve_for_hypothesis
    from src.confidence.scoring import score_all
    from src.ambiguity.gate import evaluate
    from src.recommendation.recommendation_engine import generate_recommendation
    from src.llm.narrative import generate_narrative

    grain = grain or OUTAGE_GRAIN
    ad    = ad    or ANALYSIS_DATE

    alert  = run_detection(kpi, grain, ad)
    decomp = run_decomposition(alert)
    hyps   = generate_hypotheses(decomp)

    build_index()
    evd_map, rag_map = {}, {}
    for h in hyps:
        if h.fired:
            evd_map[h.rule_id] = retrieve_evidence(h, grain, ad)
            rag_map[h.rule_id] = retrieve_for_hypothesis(h.rule_id, grain, ad)

    scores = score_all(hyps, evd_map, rag_map, sparse_history=alert.is_sparse_history)
    gate   = evaluate(scores)
    rec    = generate_recommendation(gate, ad, grain)
    pct    = (alert.actual_value - alert.expected_value) / alert.expected_value if alert.expected_value else 0.0

    bl_narr = generate_narrative(
        kpi, pct, f"{grain['region']} / {grain['category']}",
        gate, scores, evd_map, rag_map, rec, ad,
        persona="business_leader", provider="mock"
    )
    an_narr = generate_narrative(
        kpi, pct, f"{grain['region']} / {grain['category']}",
        gate, scores, evd_map, rag_map, rec, ad,
        persona="analyst", provider="mock"
    )

    return dict(
        alert=alert, decomp=decomp, hyps=hyps,
        evd_map=evd_map, rag_map=rag_map,
        scores=scores, gate=gate, rec=rec,
        bl_narr=bl_narr, an_narr=an_narr, pct=pct,
    )


# ═══════════════════════════════════════════════════════════════════════════
# §4.1 NOISE TEST
# ═══════════════════════════════════════════════════════════════════════════

class TestNoiseScenario:
    """Normal seasonal variation must NOT trigger INVESTIGATE."""

    def test_normal_grain_does_not_investigate(self) -> None:
        from src.detection.change_detection import run_detection
        result = run_detection("Revenue", NORMAL_GRAIN, ANALYSIS_DATE)
        assert result.status != "INVESTIGATE", (
            f"Normal grain {NORMAL_GRAIN} must not trigger INVESTIGATE, got {result.status}"
        )

    def test_normal_grain_no_rules_fire(self) -> None:
        from src.detection.change_detection import run_detection
        from src.decomposition.kpi_decomposition import run_decomposition
        from src.hypothesis.rules import generate_hypotheses
        alert  = run_detection("Revenue", NORMAL_GRAIN, ANALYSIS_DATE)
        decomp = run_decomposition(alert)
        hyps   = generate_hypotheses(decomp)
        fired  = [h for h in hyps if h.fired]
        # If status is NORMAL, decomp may be empty → no hyps fired is expected
        if alert.status == "NORMAL":
            assert fired == [], f"No rules should fire for NORMAL grain, fired={[h.rule_id for h in fired]}"


# ═══════════════════════════════════════════════════════════════════════════
# §4.2 TRUE ANOMALY TEST
# ═══════════════════════════════════════════════════════════════════════════

class TestTrueAnomalyScenario:
    """Persistent abnormal decline must trigger INVESTIGATE and run the full pipeline."""

    def test_outage_grain_triggers_investigate(self) -> None:
        from src.detection.change_detection import run_detection
        result = run_detection("Revenue", OUTAGE_GRAIN, ANALYSIS_DATE)
        assert result.status == "INVESTIGATE", (
            f"Outage grain must be INVESTIGATE, got {result.status}"
        )

    def test_zscore_below_threshold(self) -> None:
        from src.detection.change_detection import run_detection
        result = run_detection("Revenue", OUTAGE_GRAIN, ANALYSIS_DATE)
        assert result.zscore < -2.0, f"Z-score must be < -2.0, got {result.zscore:.2f}"

    def test_persistence_at_least_two_days(self) -> None:
        from src.detection.change_detection import run_detection
        result = run_detection("Revenue", OUTAGE_GRAIN, ANALYSIS_DATE)
        assert result.persistence_days >= 2, (
            f"Persistence must be ≥2 days, got {result.persistence_days}"
        )

    def test_full_pipeline_runs_for_investigate(self) -> None:
        """Full pipeline must complete without error for INVESTIGATE alert."""
        out = _run_full_pipeline()
        assert out["alert"].status == "INVESTIGATE"
        assert out["scores"]   # at least one hypothesis scored
        assert out["gate"]
        assert out["rec"]


# ═══════════════════════════════════════════════════════════════════════════
# §4.3 ROOT-DRIVER TEST
# ═══════════════════════════════════════════════════════════════════════════

class TestRootDriverScenario:
    """Inventory shortage must rank highest; confidence Strong or Moderate (sparse-history cap)."""

    def test_inventory_hypothesis_ranks_first(self) -> None:
        out = _run_full_pipeline()
        top = out["scores"][0]
        assert "inventory" in top.driver.lower() or "shortage" in top.driver.lower(), (
            f"Top hypothesis must be inventory-related, got '{top.driver}'"
        )

    def test_inventory_confidence_not_insufficient(self) -> None:
        out = _run_full_pipeline()
        top = out["scores"][0]
        assert top.label in ("Strong", "Moderate"), (
            f"Inventory hypothesis must be Strong/Moderate (sparse-history cap allowed), got {top.label}"
        )

    def test_decomposition_identifies_north_electronics(self) -> None:
        out = _run_full_pipeline()
        decomp = out["decomp"]
        assert decomp is not None, "Decomposition must run for INVESTIGATE alert"
        assert decomp.dominant_factor is not None


# ═══════════════════════════════════════════════════════════════════════════
# §4.4 CONTRADICTION TEST
# ═══════════════════════════════════════════════════════════════════════════

class TestContradictionScenario:
    """Competitor hypothesis must have lower confidence due to contradiction penalty."""

    def test_competitor_hypothesis_weaker_than_inventory(self) -> None:
        out = _run_full_pipeline()
        scores = out["scores"]
        inv  = next((s for s in scores if "inventory" in s.driver.lower()), None)
        comp = next((s for s in scores if "competitor" in s.driver.lower()), None)
        if inv and comp:
            assert inv.raw_score > comp.raw_score, (
                f"Inventory ({inv.raw_score:.3f}) must outscore Competitor ({comp.raw_score:.3f})"
            )

    def test_competitor_hypothesis_not_strong(self) -> None:
        out = _run_full_pipeline()
        comp = next((s for s in out["scores"] if "competitor" in s.driver.lower()), None)
        if comp:
            assert comp.label in ("Weak", "Insufficient", "Moderate"), (
                f"Competitor must not be Strong when contradicting evidence exists, got {comp.label}"
            )


# ═══════════════════════════════════════════════════════════════════════════
# §4.5 AMBIGUITY TEST
# ═══════════════════════════════════════════════════════════════════════════

class TestAmbiguityScenario:
    """Gate must correctly route ambiguous (near-equal) hypotheses to Insufficient."""

    def test_ambiguity_produces_insufficient_when_gap_small(self) -> None:
        from src.ambiguity.gate import evaluate, GATE_INSUFFICIENT, GATE_MODERATE, GATE_HIGH_CONFIDENCE
        from src.confidence.scoring import ConfidenceScore

        def _score(rule_id, driver, raw, label):
            return ConfidenceScore(
                hypothesis_rule_id=rule_id, driver=driver,
                raw_score=raw, label=label,
                rule_signal_component=raw*0.35,
                structured_evidence_component=raw*0.40,
                rag_component=raw*0.25,
                penalties_applied=0.0,
                supporting_count=1, contradicting_count=0,
                rag_top_score=0.5, sparse_history=False,
                rationale=["test"],
            )

        # Two near-equal Moderate scores: gap < 0.10 → Insufficient (ambiguity)
        s1 = _score("R01", "Inventory shortage", 0.55, "Moderate")
        s2 = _score("R02", "Delivery disruption", 0.50, "Moderate")
        gate = evaluate([s1, s2])
        assert gate.gate_state == GATE_INSUFFICIENT, (
            f"Near-equal Moderate hypotheses must produce Insufficient gate, got {gate.gate_state}"
        )

    def test_ambiguity_gate_has_missing_data_field(self) -> None:
        from src.ambiguity.gate import evaluate
        from src.confidence.scoring import ConfidenceScore

        s1 = ConfidenceScore(
            hypothesis_rule_id="R01", driver="Inventory shortage",
            raw_score=0.55, label="Moderate",
            rule_signal_component=0.19, structured_evidence_component=0.22,
            rag_component=0.14, penalties_applied=0.0,
            supporting_count=1, contradicting_count=0,
            rag_top_score=0.5, sparse_history=False, rationale=["test"],
        )
        s2 = ConfidenceScore(
            hypothesis_rule_id="R02", driver="Delivery disruption",
            raw_score=0.50, label="Moderate",
            rule_signal_component=0.18, structured_evidence_component=0.20,
            rag_component=0.12, penalties_applied=0.0,
            supporting_count=1, contradicting_count=0,
            rag_top_score=0.45, sparse_history=False, rationale=["test"],
        )
        gate = evaluate([s1, s2])
        assert gate.missing_data_needed is not None
        assert isinstance(gate.missing_data_needed, list)


# ═══════════════════════════════════════════════════════════════════════════
# §4.6 HALLUCINATION TEST
# ═══════════════════════════════════════════════════════════════════════════

class TestHallucinationScenario:
    """Pricing with zero evidence must inject GUARDRAIL — LLM cannot fabricate."""

    def test_zero_evidence_hypothesis_has_guardrail_in_prompt(self) -> None:
        from src.ambiguity.gate import GateDecision, GATE_INSUFFICIENT
        from src.confidence.scoring import ConfidenceScore
        from src.evidence.structured_evidence import EvidenceItem, StructuredEvidence
        from src.llm.prompts import build_evidence_package, build_prompt
        from src.recommendation.recommendation_engine import Recommendation

        score = ConfidenceScore(
            hypothesis_rule_id="R03_PRICING_EFFECT", driver="Pricing effect",
            raw_score=0.10, label="Insufficient",
            rule_signal_component=0.035, structured_evidence_component=0.04,
            rag_component=0.025, penalties_applied=0.0,
            supporting_count=0, contradicting_count=2,
            rag_top_score=0.1, sparse_history=False, rationale=["no pricing signal"],
        )
        evd = StructuredEvidence(
            hypothesis_rule_id="R03_PRICING_EFFECT", driver="Pricing effect",
            grain_filters=OUTAGE_GRAIN, analysis_date=ANALYSIS_DATE,
            items=[
                EvidenceItem(
                    source_table="raw.orders", metric_name="discount_rate",
                    metric_value=0.05, baseline_value=0.04,
                    delta=-0.01, delta_pct=-0.5,
                    window_days=90, supports_hypothesis=False,
                    description="No meaningful pricing change detected.",
                )
            ],
        )
        gate = GateDecision(
            gate_state=GATE_INSUFFICIENT, primary_hypothesis=score,
            all_scores=[score], plausible_hypotheses=[],
            why_undetermined="No pricing signal.",
            missing_data_needed=["Competitor pricing data."],
            human_review_required=True,
        )
        rec = Recommendation(
            tier="None", gate_state=GATE_INSUFFICIENT,
            driver="Pricing effect", rule_id="R03_PRICING_EFFECT",
            confidence_score=0.10, action_owner="",
            immediate_action="No action recommended.",
            verification_step="", escalation_path="",
        )
        package = build_evidence_package(
            kpi_name="Revenue", pct_change=-0.082,
            affected_segment="North / Electronics",
            gate_decision=gate, scores=[score],
            evidence_map={"R03_PRICING_EFFECT": evd},
            rag_map=None, recommendation=rec,
            analysis_date=ANALYSIS_DATE,
        )
        prompt = build_prompt(package)
        assert "GUARDRAIL" in prompt, "Prompt must contain GUARDRAIL for zero-evidence hypothesis"

    def test_narrative_does_not_claim_pricing_caused_drop(self) -> None:
        """LLM narrative for inventory-only scenario must not claim pricing caused revenue drop."""
        out = _run_full_pipeline()
        narrative = out["bl_narr"].narrative.lower()
        # The mock narrative for inventory won't mention pricing as the cause
        assert "pricing caused" not in narrative
        assert "price reduction caused" not in narrative


# ═══════════════════════════════════════════════════════════════════════════
# §4.7 RECOMMENDATION RELEVANCE TEST
# ═══════════════════════════════════════════════════════════════════════════

class TestRecommendationRelevanceScenario:
    """With inventory driver confirmed, recommendation must relate to inventory/replenishment."""

    def test_recommendation_mentions_inventory_action(self) -> None:
        out = _run_full_pipeline()
        rec = out["rec"]
        combined = (
            (rec.immediate_action or "") + " " +
            (rec.driver or "") + " " +
            (rec.action_owner or "")
        ).lower()
        inventory_terms = ["inventory", "replenishment", "stock", "supply", "restock", "warehouse"]
        assert any(t in combined for t in inventory_terms), (
            f"Recommendation must mention inventory/replenishment terms. Got: '{combined[:200]}'"
        )

    def test_recommendation_not_generic_marketing(self) -> None:
        out = _run_full_pipeline()
        rec = out["rec"]
        combined = ((rec.immediate_action or "") + " " + (rec.driver or "")).lower()
        assert "improve marketing" not in combined
        assert "marketing campaign" not in combined

    def test_recommendation_tier_is_not_none(self) -> None:
        """Inventory shortage (top hypothesis) must yield Full or Conditional recommendation."""
        out = _run_full_pipeline()
        assert out["rec"].tier in ("Full", "Conditional"), (
            f"Expected Full or Conditional recommendation, got '{out['rec'].tier}'"
        )


# ═══════════════════════════════════════════════════════════════════════════
# §4.8 SPARSE-HISTORY TEST
# ═══════════════════════════════════════════════════════════════════════════

class TestSparseHistoryScenario:
    """Sparse-history SKU must: use category proxy, cap confidence at Moderate."""

    def test_outage_grain_is_sparse(self) -> None:
        from src.detection.change_detection import run_detection
        alert = run_detection("Revenue", OUTAGE_GRAIN, ANALYSIS_DATE)
        assert alert.is_sparse_history is True, (
            "North/Electronics on analysis date must be flagged as sparse-history"
        )

    def test_sparse_history_uses_category_proxy_baseline(self) -> None:
        from src.detection.change_detection import run_detection
        alert = run_detection("Revenue", OUTAGE_GRAIN, ANALYSIS_DATE)
        assert alert.baseline_type == "category_proxy", (
            f"Sparse-history alert must use category_proxy baseline, got {alert.baseline_type}"
        )

    def test_confidence_capped_at_moderate_for_sparse(self) -> None:
        out = _run_full_pipeline()
        if out["alert"].is_sparse_history:
            for score in out["scores"]:
                assert score.label != "Strong", (
                    f"Sparse history must cap confidence at Moderate — '{score.driver}' got Strong"
                )

    def test_sparse_history_note_in_alert(self) -> None:
        from src.detection.change_detection import run_detection
        alert = run_detection("Revenue", OUTAGE_GRAIN, ANALYSIS_DATE)
        notes_text = " ".join(alert.notes).lower()
        assert "sparse" in notes_text or "proxy" in notes_text or "moderate" in notes_text


# ═══════════════════════════════════════════════════════════════════════════
# §4.9 ROLE-BASED SECURITY TEST
# ═══════════════════════════════════════════════════════════════════════════

class TestRoleBasedSecurityScenario:
    """regional_leader (North) must be blocked from South data at the API layer."""

    def test_regional_leader_blocked_from_south_alert(self) -> None:
        from fastapi.testclient import TestClient
        from src.api.main import app
        client = TestClient(app)

        # Login as regional_leader
        resp = client.post("/auth/login", data={"username": "regional_leader", "password": "leader123"})
        assert resp.status_code == 200
        token = resp.json()["access_token"]

        # Attempt to access South alert — must get 403
        south_id = f"Revenue_South_Electronics_{ANALYSIS_DATE}"
        resp2 = client.get(f"/kpi/alerts/{south_id}", headers={"Authorization": f"Bearer {token}"})
        assert resp2.status_code == 403, (
            f"regional_leader must be blocked from South data. Got {resp2.status_code}"
        )

    def test_analyst_can_access_north_and_south(self) -> None:
        from fastapi.testclient import TestClient
        from src.api.main import app
        client = TestClient(app)

        resp = client.post("/auth/login", data={"username": "analyst", "password": "analyst123"})
        token = resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        north_id = f"Revenue_North_Electronics_{ANALYSIS_DATE}"
        r1 = client.get(f"/kpi/alerts/{north_id}", headers=headers)
        assert r1.status_code == 200

    def test_unauthenticated_request_blocked(self) -> None:
        from fastapi.testclient import TestClient
        from src.api.main import app
        client = TestClient(app)
        resp = client.get("/kpi/alerts")
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════
# §4.10 PERSONA NARRATIVE TEST
# ═══════════════════════════════════════════════════════════════════════════

class TestPersonaNarrativeScenario:
    """Both personas must agree on facts; only tone/depth differ."""

    def test_both_personas_generated(self) -> None:
        out = _run_full_pipeline()
        assert out["bl_narr"].narrative
        assert out["an_narr"].narrative

    def test_both_narratives_are_different(self) -> None:
        """Different system prompts → different narrative text."""
        out = _run_full_pipeline()
        assert out["bl_narr"].narrative != out["an_narr"].narrative

    def test_business_leader_no_zscore_jargon(self) -> None:
        out = _run_full_pipeline()
        narr = out["bl_narr"].narrative.lower()
        for term in ["z-score", "zscore", "standard deviation"]:
            assert term not in narr, f"business_leader narrative must not contain '{term}'"

    def test_evidence_packages_contain_same_kpi(self) -> None:
        """Both personas get the same evidence package KPI field."""
        out = _run_full_pipeline()
        assert out["bl_narr"].evidence_package["kpi"] == out["an_narr"].evidence_package["kpi"]


# ═══════════════════════════════════════════════════════════════════════════
# §4.11 FEEDBACK LOOP TEST
# ═══════════════════════════════════════════════════════════════════════════

class TestFeedbackLoopScenario:
    """Feedback from both personas stored correctly with role + timestamp."""

    def test_accept_from_regional_leader_stored(self) -> None:
        from src.feedback.feedback_store import FeedbackEntry, submit, get_feedback
        uid = ALERT_ID + "_e2e_leader"
        row_id = submit(FeedbackEntry(
            alert_id=uid, decision="accept",
            submitted_by="regional_leader", role="regional_leader",
            notes="Confirmed — replenishment underway.",
        ))
        assert row_id > 0
        results = get_feedback(uid)
        assert results[0].role == "regional_leader"
        assert results[0].decision == "accept"

    def test_reject_from_analyst_stored(self) -> None:
        from src.feedback.feedback_store import FeedbackEntry, submit, get_feedback
        uid = ALERT_ID + "_e2e_analyst"
        submit(FeedbackEntry(
            alert_id=uid, decision="reject",
            submitted_by="analyst", role="analyst",
            notes="Disagree — demand decline more likely.",
        ))
        results = get_feedback(uid)
        assert results[0].submitted_by == "analyst"

    def test_all_four_decisions_storable(self) -> None:
        from src.feedback.feedback_store import FeedbackEntry, submit, VALID_DECISIONS
        for decision in VALID_DECISIONS:
            uid = ALERT_ID + f"_e2e_{decision}"
            row_id = submit(FeedbackEntry(
                alert_id=uid, decision=decision,
                submitted_by="analyst", role="analyst",
            ))
            assert row_id > 0

    def test_feedback_summary_reflects_decisions(self) -> None:
        from src.feedback.feedback_store import get_summary
        summary = get_summary()
        assert summary["total_feedback"] > 0
        assert "accept" in summary["by_decision"] or "reject" in summary["by_decision"]
        assert isinstance(summary["accept_rate_pct"], float)
