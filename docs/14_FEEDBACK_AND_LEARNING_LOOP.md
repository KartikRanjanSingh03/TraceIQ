# TraceIQ — Feedback & Learning Loop

## 1. Purpose
Round 2 requires a "mechanism to learn from analyst and business-user feedback." This doc
defines what's captured, how it's stored, and how it would improve the system over time —
scoped honestly for MVP (log + inform manual tuning) vs a stated future direction (automated
recalibration).

## 2. What Gets Captured
Every human decision on a recommendation (per `02_ARCHITECTURE.md` §3.12) is logged via the
`/feedback` endpoint into `feedback_log` (`05_DATA_SCHEMA.md` §3.5):
- Which recommendation/hypothesis it relates to
- The decision: accept / reject / edit / request_more_evidence
- Optional free-text comment (e.g. why rejected)
- Submitting user's ID and role (for audit trail and pattern analysis by persona)
- Timestamp
- `actual_outcome` — filled in later if/when the real-world result is known (e.g. did revenue
  recover after the recommended action was taken)

## 3. MVP Scope: Logging Only (explicitly honest)
For the prototype, the feedback loop is a **capture and display mechanism**, not a live
retraining system. This is a deliberate, disclosed MVP boundary — building real automated
recalibration (e.g. reinforcement learning on thresholds) is out of scope, consistent with the
Round 1 blueprint's explicit guidance: *"Don't build complex reinforcement learning initially...
simply logging feedback is enough."*

## 4. What the Logged Data Would Improve (documented, not built, in MVP)
| Feedback pattern | Would inform |
|---|---|
| A hypothesis type (e.g. "Pricing effect") is frequently rejected by analysts | Re-examine or down-weight that rule in `hypothesis/rules.py` |
| Recommendations for a driver are often "edited" before acceptance | Refine the recommendation template wording for that driver |
| "Request more evidence" is common for a specific evidence dimension | Add a new structured/unstructured data source for that gap |
| Confidence label frequently disagrees with human judgment (e.g. many Strong→Rejected) | Re-tune weights in `confidence/scoring.py` (07 §6.1) |
| Anomalies frequently marked "not meaningful" by humans | Adjust statistical/materiality thresholds in `04_KPI_SEMANTIC_CONTRACT.md` §5 |

## 5. MVP Demonstration
The prototype includes a simple **feedback summary view** (in the Analyst persona UI, or a
dedicated admin screen) showing: accept/reject/edit counts per driver type, over the demo
dataset's history. This demonstrates the loop exists and is populated, without claiming the
system has auto-learned from it yet.

## 6. Future Direction (stated, not implemented)
If extended past MVP:
- Periodic batch job re-computes confidence-scoring weights using logged
  accept/reject outcomes (supervised calibration, not full RL)
- A/B or shadow-mode testing of threshold changes before promoting them
- Feedback-informed hypothesis rule suggestions reviewed by a human before being added to
  `hypothesis/rules.py` (human-in-the-loop rule evolution, not autonomous self-modification)
This section exists so it's clear the team understands the full loop, even though MVP
intentionally stops at logging.

## 7. Data Retention & Privacy Note
Feedback log contains user_id and role — no customer PII, consistent with `11_SECURITY_AND_API.md`
§6. Retained indefinitely for MVP (no retention policy needed at prototype scale).