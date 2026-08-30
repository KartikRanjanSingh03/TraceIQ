"""
tests/test_stage11_api.py — Stage 11 required tests (17_ROADMAP.md §2 Stage 11).

Required per 15_TESTING_STRATEGY.md:
  §4.12 Role-Based Security Test:
    - regional_leader (North) cannot access South alerts
    - analyst can access all regions
    - Unauthenticated request to protected endpoint → 401
    - regional_leader cannot call /pipeline/run → 403
    - analyst CAN call /pipeline/run → 200

Also tests:
  - /auth/login with correct credentials → JWT token issued
  - /auth/login with wrong password → 401
  - /kpi/alerts returns list (regional_leader sees only North)
  - /kpi/alerts/{alert_id} returns AlertDetail
  - /feedback POST returns accepted status

Run:
    pytest tests/test_stage11_api.py -v
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.main import app

client = TestClient(app)

ANALYSIS_DATE = "2026-08-29"
NORTH_ALERT_ID = f"Revenue_North_Electronics_{ANALYSIS_DATE}"
SOUTH_ALERT_ID = f"Revenue_South_Electronics_{ANALYSIS_DATE}"


# ── Auth helpers ────────────────────────────────────────────────────────────

def _login(username: str, password: str) -> str:
    """Returns JWT token or raises on failure."""
    resp = client.post("/auth/login", data={"username": username, "password": password})
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def leader_token():
    return _login("regional_leader", "leader123")


@pytest.fixture(scope="module")
def analyst_token():
    return _login("analyst", "analyst123")


# ═══════════════════════════════════════════════════════════════════════════
# AUTH
# ═══════════════════════════════════════════════════════════════════════════

class TestAuth:
    def test_login_regional_leader_succeeds(self) -> None:
        token = _login("regional_leader", "leader123")
        assert token

    def test_login_analyst_succeeds(self) -> None:
        token = _login("analyst", "analyst123")
        assert token

    def test_login_wrong_password_gives_401(self) -> None:
        resp = client.post("/auth/login", data={"username": "analyst", "password": "wrong"})
        assert resp.status_code == 401

    def test_login_unknown_user_gives_401(self) -> None:
        resp = client.post("/auth/login", data={"username": "ghost", "password": "anything"})
        assert resp.status_code == 401

    def test_unauthenticated_alerts_gives_401(self) -> None:
        """§4.12 core: unauthenticated request to protected endpoint → 401."""
        resp = client.get("/kpi/alerts")
        assert resp.status_code == 401

    def test_unauthenticated_pipeline_run_gives_401(self) -> None:
        resp = client.post("/pipeline/run")
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════
# §4.12 ROLE-BASED SECURITY (CORE)
# ═══════════════════════════════════════════════════════════════════════════

class TestRoleBasedSecurity:
    """
    11_SECURITY §5: Regional Leader can only fetch alerts for their own region.
    Only Analyst/Admin can trigger /pipeline/run.
    """

    def test_regional_leader_cannot_call_pipeline_run(self, leader_token) -> None:
        """§4.12: regional_leader calling /pipeline/run → 403."""
        resp = client.post(
            "/pipeline/run",
            params={"region": "North", "category": "Electronics"},
            headers=_auth(leader_token),
        )
        assert resp.status_code == 403, (
            f"regional_leader must get 403 on /pipeline/run, got {resp.status_code}"
        )

    def test_analyst_can_call_pipeline_run(self, analyst_token) -> None:
        """§4.12: analyst calling /pipeline/run → 200."""
        resp = client.post(
            "/pipeline/run",
            params={"region": "North", "category": "Electronics",
                    "analysis_date": ANALYSIS_DATE},
            headers=_auth(analyst_token),
        )
        assert resp.status_code == 200, (
            f"analyst must get 200 on /pipeline/run, got {resp.status_code}: {resp.text}"
        )

    def test_regional_leader_cannot_access_south_alert(self, leader_token) -> None:
        """§4.12: regional_leader (North) cannot fetch South alert → 403."""
        resp = client.get(f"/kpi/alerts/{SOUTH_ALERT_ID}", headers=_auth(leader_token))
        assert resp.status_code == 403, (
            f"regional_leader (North) must get 403 for South alert, got {resp.status_code}"
        )

    def test_analyst_can_access_north_alert(self, analyst_token) -> None:
        """Analyst (all regions) can fetch North alert."""
        resp = client.get(
            f"/kpi/alerts/{NORTH_ALERT_ID}", headers=_auth(analyst_token)
        )
        assert resp.status_code == 200, (
            f"analyst must get 200 for North alert, got {resp.status_code}: {resp.text}"
        )

    def test_regional_leader_can_access_own_region_alert(self, leader_token) -> None:
        """Regional leader can access their own region's alert."""
        resp = client.get(f"/kpi/alerts/{NORTH_ALERT_ID}", headers=_auth(leader_token))
        assert resp.status_code == 200

    def test_telemetry_blocked_for_regional_leader(self, leader_token) -> None:
        """§5: /telemetry/summary is analyst-only."""
        resp = client.get("/telemetry/summary", headers=_auth(leader_token))
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════
# ALERTS LIST
# ═══════════════════════════════════════════════════════════════════════════

class TestAlertsList:
    def test_analyst_sees_alerts(self, analyst_token) -> None:
        resp = client.get(
            "/kpi/alerts",
            params={"analysis_date": ANALYSIS_DATE},
            headers=_auth(analyst_token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_alerts_have_required_fields(self, analyst_token) -> None:
        resp = client.get(
            "/kpi/alerts",
            params={"analysis_date": ANALYSIS_DATE},
            headers=_auth(analyst_token),
        )
        for alert in resp.json():
            assert "alert_id"  in alert
            assert "kpi"       in alert
            assert "region"    in alert
            assert "status"    in alert
            assert "pct_change" in alert

    def test_regional_leader_only_sees_north(self, leader_token) -> None:
        resp = client.get(
            "/kpi/alerts",
            params={"analysis_date": ANALYSIS_DATE},
            headers=_auth(leader_token),
        )
        assert resp.status_code == 200
        for alert in resp.json():
            assert alert["region"] == "North", (
                f"regional_leader must only see North alerts, got {alert['region']}"
            )


# ═══════════════════════════════════════════════════════════════════════════
# NARRATIVE & RECOMMENDATION ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

class TestNarrativeAndRecommendation:
    def test_narrative_endpoint_returns_text(self, analyst_token) -> None:
        resp = client.get(
            f"/kpi/alerts/{NORTH_ALERT_ID}/narrative",
            params={"persona": "business_leader"},
            headers=_auth(analyst_token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "narrative" in data
        assert data["narrative"]

    def test_recommendation_endpoint_returns_structured(self, analyst_token) -> None:
        resp = client.get(
            f"/kpi/alerts/{NORTH_ALERT_ID}/recommendation",
            headers=_auth(analyst_token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "tier" in data
        assert "action_owner" in data
        assert "immediate_action" in data


# ═══════════════════════════════════════════════════════════════════════════
# FEEDBACK
# ═══════════════════════════════════════════════════════════════════════════

class TestFeedback:
    def test_feedback_post_accepted(self, analyst_token) -> None:
        resp = client.post(
            "/feedback",
            params={"alert_id": NORTH_ALERT_ID, "decision": "accept"},
            headers=_auth(analyst_token),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "accepted"

    def test_feedback_tags_user_role(self, leader_token) -> None:
        resp = client.post(
            "/feedback",
            params={"alert_id": NORTH_ALERT_ID, "decision": "reject",
                    "notes": "Disagree with hypothesis"},
            headers=_auth(leader_token),
        )
        data = resp.json()
        assert data["submitted_by"] == "regional_leader"
        assert data["role"] == "regional_leader"
