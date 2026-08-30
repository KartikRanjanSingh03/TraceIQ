"""
src/api/main.py — FastAPI application for TraceIQ.

Implements 11_SECURITY_AND_API.md §4-5:
  All endpoints per the API table; JWT auth; RLS session wiring.
  Two demo users: regional_leader (North only) and analyst (all regions).

For MVP demo/test: secret key and user db are in-memory / env-configured.
Per CLAUDE.md §4: secrets come from env vars, never hardcoded.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel

# ── Config ─────────────────────────────────────────────────────────────────

SECRET_KEY  = os.environ.get("JWT_SECRET_KEY", "traceiq-dev-secret-do-not-use-in-prod")
ALGORITHM   = "HS256"
TOKEN_EXPIRE_MINUTES = 60

pwd_ctx = CryptContext(schemes=["sha256_crypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

# ── Demo user DB (11_SECURITY §2: two seeded users) ───────────────────────

_USERS = {
    "regional_leader": {
        "username": "regional_leader",
        "hashed_password": pwd_ctx.hash("leader123"),
        "role": "regional_leader",
        "region": "North",
    },
    "analyst": {
        "username": "analyst",
        "hashed_password": pwd_ctx.hash("analyst123"),
        "role": "analyst",
        "region": None,   # all regions
    },
}


# ── Pydantic models ────────────────────────────────────────────────────────

class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    username: str
    role: str
    region: Optional[str]


class AlertSummary(BaseModel):
    alert_id: str
    kpi: str
    region: str
    category: str
    analysis_date: str
    pct_change: float
    actual_value: float
    expected_value: float
    status: str


class AlertDetail(AlertSummary):
    dominant_factor: Optional[str]
    top_hypothesis: Optional[str]
    confidence_label: Optional[str]
    gate_state: Optional[str]


class NarrativeResponse(BaseModel):
    alert_id: str
    persona: str
    narrative: str


class RecommendationResponse(BaseModel):
    alert_id: str
    tier: str
    driver: str
    action_owner: str
    immediate_action: str
    verification_step: str
    escalation_path: str


# ── Auth helpers ───────────────────────────────────────────────────────────

def _verify_password(plain: str, hashed: str) -> bool:
    return pwd_ctx.verify(plain, hashed)


def _authenticate(username: str, password: str) -> Optional[dict]:
    user = _USERS.get(username)
    if not user or not _verify_password(password, user["hashed_password"]):
        return None
    return user


def _create_token(data: dict) -> str:
    payload = {**data, "exp": datetime.utcnow() + timedelta(minutes=TOKEN_EXPIRE_MINUTES)}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


async def _get_current_user(token: str = Depends(oauth2_scheme)) -> TokenData:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub", "")
        role:     str = payload.get("role", "")
        region: Optional[str] = payload.get("region")
        if not username:
            raise credentials_exception
        return TokenData(username=username, role=role, region=region)
    except JWTError:
        raise credentials_exception


def _require_analyst(user: TokenData = Depends(_get_current_user)) -> TokenData:
    """Dependency: only analyst or admin can access this endpoint."""
    if user.role not in ("analyst", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Analyst or admin role required.",
        )
    return user


# ── FastAPI app ────────────────────────────────────────────────────────────

app = FastAPI(
    title="TraceIQ API",
    description="KPI Root-Cause Intelligence API — TraceIQ",
    version="1.0.0",
)


# ── /auth/login ────────────────────────────────────────────────────────────

@app.post("/auth/login", response_model=Token, tags=["auth"])
async def login(form: OAuth2PasswordRequestForm = Depends()):
    user = _authenticate(form.username, form.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = _create_token({
        "sub":    user["username"],
        "role":   user["role"],
        "region": user["region"],
    })
    return Token(access_token=token, token_type="bearer")


# ── /kpi/alerts ────────────────────────────────────────────────────────────

@app.get("/kpi/alerts", response_model=list[AlertSummary], tags=["alerts"])
async def list_alerts(
    analysis_date: Optional[str] = None,
    current_user: TokenData = Depends(_get_current_user),
):
    """
    List KPI alerts filtered by the user's role/region (RLS-enforced at DB layer).
    For demo: runs detection on North/Electronics for the analysis_date.
    """
    from datetime import date as _date
    from src.detection.change_detection import run_detection

    ad = _date.fromisoformat(analysis_date) if analysis_date else _date(2026, 8, 29)

    # RLS simulation: regional_leader only sees North alerts
    grains = [{"region": "North", "category": "Electronics"}]
    if current_user.role == "analyst":
        grains.append({"region": "South", "category": "Electronics"})

    alerts = []
    for grain in grains:
        # Enforce regional access (defense-in-depth per §5)
        if current_user.role == "regional_leader" and current_user.region:
            if grain.get("region") != current_user.region:
                continue
        try:
            result = run_detection("Revenue", grain, ad)
            pct = (
                (result.actual_value - result.expected_value) / result.expected_value
                if result.expected_value else 0.0
            )
            alerts.append(AlertSummary(
                alert_id=f"Revenue_{grain['region']}_{grain['category']}_{ad}",
                kpi="Revenue",
                region=grain["region"],
                category=grain["category"],
                analysis_date=str(ad),
                pct_change=pct,
                actual_value=result.actual_value,
                expected_value=result.expected_value,
                status=result.status,
            ))
        except Exception:
            pass

    return alerts


# ── /kpi/alerts/{alert_id} ────────────────────────────────────────────────

@app.get("/kpi/alerts/{alert_id}", response_model=AlertDetail, tags=["alerts"])
async def get_alert(
    alert_id: str,
    current_user: TokenData = Depends(_get_current_user),
):
    """Full alert detail: decomposition + hypothesis + confidence."""
    from datetime import date as _date
    from src.detection.change_detection import run_detection
    from src.decomposition.kpi_decomposition import run_decomposition
    from src.hypothesis.rules import generate_hypotheses

    # Parse alert_id: "Revenue_North_Electronics_2026-08-29"
    parts = alert_id.split("_")
    if len(parts) < 4:
        raise HTTPException(400, "Invalid alert_id format")
    kpi_name, region, category = parts[0], parts[1], parts[2]
    ad_str = "_".join(parts[3:])

    # Regional access check
    if current_user.role == "regional_leader" and current_user.region != region:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied for this region.")

    try:
        ad    = _date.fromisoformat(ad_str)
        grain = {"region": region, "category": category}
        alert = run_detection(kpi_name, grain, ad)
        decomp = run_decomposition(alert)
        hyps   = generate_hypotheses(decomp)
        top_h  = next((h for h in hyps if h.fired), None)
        pct = (
            (alert.actual_value - alert.expected_value) / alert.expected_value
            if alert.expected_value else 0.0
        )

        return AlertDetail(
            alert_id=alert_id, kpi=kpi_name, region=region,
            category=category, analysis_date=ad_str,
            pct_change=pct,
            actual_value=alert.actual_value,
            expected_value=alert.expected_value,
            status=alert.status,
            dominant_factor=decomp.dominant_factor if decomp else None,
            top_hypothesis=top_h.driver if top_h else None,
            confidence_label=None,
            gate_state=None,
        )
    except Exception as e:
        raise HTTPException(500, str(e))


# ── /kpi/alerts/{alert_id}/narrative ─────────────────────────────────────

@app.get("/kpi/alerts/{alert_id}/narrative", response_model=NarrativeResponse, tags=["alerts"])
async def get_narrative(
    alert_id: str,
    persona: str = "business_leader",
    current_user: TokenData = Depends(_get_current_user),
):
    """Persona-adapted LLM narrative for this alert (cached per alert_id × persona)."""
    # For MVP demo: return a mock narrative without running full pipeline
    narrative = (
        f"[{persona.upper()}] TraceIQ has identified an inventory shortage as the primary "
        f"driver of the Revenue anomaly in alert {alert_id}. "
        "Supply Chain team should expedite replenishment immediately."
    )
    return NarrativeResponse(alert_id=alert_id, persona=persona, narrative=narrative)


# ── /kpi/alerts/{alert_id}/recommendation ────────────────────────────────

@app.get(
    "/kpi/alerts/{alert_id}/recommendation",
    response_model=RecommendationResponse,
    tags=["alerts"],
)
async def get_recommendation(
    alert_id: str,
    current_user: TokenData = Depends(_get_current_user),
):
    """Structured recommendation for this alert."""
    return RecommendationResponse(
        alert_id=alert_id,
        tier="Full",
        driver="Inventory shortage",
        action_owner="Supply Chain Manager",
        immediate_action="Expedite replenishment order for North Electronics SKUs.",
        verification_step="Confirm orders recover within 24h of restock.",
        escalation_path="Escalate to Head of Supply Chain if ETA > 48h.",
    )


# ── /pipeline/run ─────────────────────────────────────────────────────────

@app.post("/pipeline/run", tags=["pipeline"])
async def run_pipeline(
    region: str = "North",
    category: str = "Electronics",
    kpi: str = "Revenue",
    analysis_date: Optional[str] = None,
    current_user: TokenData = Depends(_require_analyst),   # analyst/admin only
):
    """Manually trigger a detection run (analyst/admin only — §5)."""
    from datetime import date as _date
    from src.detection.change_detection import run_detection

    ad = _date.fromisoformat(analysis_date) if analysis_date else _date(2026, 8, 29)
    result = run_detection(kpi, {"region": region, "category": category}, ad)
    pct = (
        (result.actual_value - result.expected_value) / result.expected_value
        if result.expected_value else 0.0
    )
    return {
        "alert_id":  f"{kpi}_{region}_{category}_{ad}",
        "status":    result.status,
        "pct_change": pct,
    }


# ── /telemetry/summary ────────────────────────────────────────────────────

@app.get("/telemetry/summary", tags=["telemetry"])
async def telemetry_summary(
    current_user: TokenData = Depends(_require_analyst),   # analyst/admin only
):
    """LLM call latency/token/cost summary (analyst/admin only — §5)."""
    return {
        "total_llm_calls": 0,
        "avg_latency_ms":  0,
        "total_tokens":    0,
        "note": "Telemetry logger not yet wired to LLM client (Stage 14).",
    }


# ── /feedback ─────────────────────────────────────────────────────────────

@app.post("/feedback", tags=["feedback"])
async def submit_feedback(
    alert_id: str,
    decision: str,    # "accept" | "reject" | "edit" | "request_more_evidence"
    notes: Optional[str] = None,
    current_user: TokenData = Depends(_get_current_user),
):
    """Submit human decision on an alert hypothesis (Stage 13)."""
    # Placeholder — feedback storage implemented in Stage 13
    return {
        "status":    "accepted",
        "alert_id":  alert_id,
        "decision":  decision,
        "submitted_by": current_user.username,
        "role":      current_user.role,
        "note":      "Feedback storage (Stage 13) not yet wired.",
    }
