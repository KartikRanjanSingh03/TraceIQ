# TraceIQ — Testing Strategy

## 1. Purpose
Defines how each layer is validated before moving to the next (per the staged build order in
`17_ROADMAP.md`), plus the specific end-to-end test scenarios required to prove the system
meets Round 2's minimum expectations. Directly incorporates the Round 1 blueprint's "Testing
Plan" section.

## 2. Testing Philosophy
- Every module in `13_BACKEND_MODULE_PLAN.md` gets unit tests before the next module is built
  — bugs are caught at the smallest possible scope, not after full integration.
- End-to-end scenario tests run against the synthetic dataset (`01_PRD.md` §6) with known,
  injected ground truth, so "correct" is objectively checkable, not subjective.
- The LLM layer is tested last and separately, since it's the only non-deterministic component.

## 3. Unit Test Coverage per Module
| Module | Key test cases |
|---|---|
| `detection/change_detection.py` | Known-normal input → NORMAL; known-anomalous input → INVESTIGATE; single-day spike → WATCH not INVESTIGATE (persistence check) |
| `decomposition/kpi_decomposition.py` | Formula decomposition sums correctly; contribution ranking matches manually-computed expected order |
| `hypothesis/rules.py` | Each rule fires on its exact trigger condition and does not fire otherwise |
| `evidence/structured_evidence.py` | Returns correct rows for a known query window/segment |
| `evidence/retrieval.py` | Semantic query returns the deliberately-planted relevant document in top-k |
| `confidence/scoring.py` | Known evidence combinations map to expected confidence labels |
| `ambiguity/gate.py` | Strong→High path, Moderate→conditional path, Insufficient→abstention path all route correctly |
| `recommendation/recommendation_engine.py` | Only Strong/Moderate hypotheses get a recommendation; Weak/Insufficient get `None` (per `13_BACKEND_MODULE_PLAN.md` §4) |
| `llm/narrative.py` | Output contains no claim absent from the input evidence package (checked via keyword/fact presence, not exact match) |

## 4. Required End-to-End Scenario Tests (from Round 1 blueprint + Round 2 minimum expectations)

### 4.1 Noise Test
Feed normal seasonal variation. **Expected**: no INVESTIGATE alert triggered, no unnecessary
investigation pipeline run.

### 4.2 True Anomaly Test
Feed a persistent abnormal decline (≥2 consecutive days, per `07_ANALYTICS_AND_DRIVER_ANALYSIS.md`
§2.3). **Expected**: INVESTIGATE triggered, full pipeline runs.

### 4.3 Root-Driver Test
Inject a known inventory issue (stockouts + outage ticket) into the synthetic data.
**Expected**: Inventory shortage hypothesis ranks highest, confidence label = Strong.

### 4.4 Contradiction Test
Add a competitor-activity signal where affected segments don't align with the KPI movement's
segment. **Expected**: Competitor hypothesis confidence is pulled down (Weak/Insufficient) due
to the contradiction penalty (`07` §6.1).

### 4.5 Ambiguity Test
Construct two equally plausible explanations with no distinguishing evidence.
**Expected**: `Insufficient Evidence` output with both hypotheses listed and missing-data
statement — not a forced, falsely confident answer. Validates `08_CONFIDENCE_AND_ABSTENTION.md`.

### 4.6 Hallucination Test
Ask the system "Was pricing responsible?" when no pricing signal exists in the evidence
package at all. **Expected**: explicit "no evidence supports this" response — not an invented
explanation. Validates `09_LLM_ARCHITECTURE.md` §10.

### 4.7 Recommendation Relevance Test
With the inventory driver confirmed (Strong), check the recommendation text.
**Expected**: recommendation specifically relates to inventory/replenishment — not generic
advice (e.g. not "improve marketing").

### 4.8 Sparse-History Test
Query the newly-launched SKU scenario (`01_PRD.md` §6.2, <30 days history).
**Expected**: system uses category-level proxy baseline, confidence is capped at Moderate, and
the UI shows the "limited history" caveat (`12_FRONTEND_UX_PLAN.md` §5).

### 4.9 Role-Based Security Test
Log in as `regional_leader` (North) and attempt to query South-region data via the API
directly (bypassing UI). **Expected**: RLS blocks the row-level access regardless of API
request parameters (`10_DATABASE_DESIGN.md` §5, `11_SECURITY_AND_API.md` §3).

### 4.10 Persona Narrative Test
Generate narratives for the same alert as both personas. **Expected**: both narratives agree
on all facts and confidence levels; only tone/depth/length differ (`09_LLM_ARCHITECTURE.md` §7).

### 4.11 Feedback Loop Test

Submit accept/reject/edit/request_more_evidence feedback for a recommendation as both
Regional Leader and Analyst personas.

Expected: feedback is successfully stored in `feedback_log` with the correct
`recommendation_id`, `user_id`, `user_role`, decision, and timestamp, and the feedback
summary view reflects the recorded decision.

This validates the Round 2 feedback-and-learning-loop requirement
(`14_FEEDBACK_AND_LEARNING_LOOP.md`).

## 5. Regression Safety
All scenario tests in §4 are run automatically before any change is considered complete for a
given roadmap stage (`17_ROADMAP.md`) — a stage is not marked done in
`00_IMPLEMENTATION_PROGRESS.md` until its relevant tests pass.

## 6. Test Data
All scenario tests run against the deterministic synthetic dataset (`data/generate_synthetic_data.py`,
per `13_BACKEND_MODULE_PLAN.md` §2) with a fixed random seed, so results are reproducible across
runs and across whichever model/session (Claude, Gemini, Antigravity) executes them.