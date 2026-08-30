# TraceIQ — Security & API Design

## 1. Purpose
Covers authentication, authorization, API endpoint design, and how the role-based
security/entitlement scenario (Round 2 minimum expectation) is enforced end-to-end, from login
through to the database RLS policies defined in `10_DATABASE_DESIGN.md`.

## 2. MVP Auth Approach (kept deliberately simple)
- Two hardcoded/seeded demo users for the prototype: one `regional_leader` (region = North),
  one `analyst` (all regions) — matches the personas in `01_PRD.md` §5.
- Simple username/password login via FastAPI, issuing a JWT containing `user_id`, `role`, and
  `region` claims.
- No OAuth/SSO integration in MVP — explicitly out of scope, consistent with `01_PRD.md` §3.
- Password hashing via `passlib`/`bcrypt` (free, standard) even for the demo, so the auth flow
  is realistic rather than a toy.

## 3. Session → Database Security Chain
```
Login (username/password)
   ↓
JWT issued: {user_id, role, region}
   ↓
FastAPI dependency extracts JWT on each request
   ↓
Sets Postgres session variable: SET app.current_user_region = '<region from JWT>'
   ↓
Query runs as the role's DB user (regional_leader_role / analyst_role)
   ↓
RLS policy (10_DATABASE_DESIGN.md §5) filters rows automatically
   ↓
Correct column-level view (10_DATABASE_DESIGN.md §6) selected based on role
```
This means security is enforced at the database layer, not just in application code — even if
an API endpoint had a bug, RLS would still prevent cross-region data leakage.

## 4. Core API Endpoints (FastAPI)

| Endpoint | Method | Purpose | Auth required |
|---|---|---|---|
| `/auth/login` | POST | Issue JWT | No |
| `/kpi/alerts` | GET | List current KPI alerts (filtered by role/region via RLS) | Yes |
| `/kpi/alerts/{alert_id}` | GET | Full alert detail: decomposition, hypotheses, evidence | Yes |
| `/kpi/alerts/{alert_id}/narrative` | GET | Persona-adapted LLM narrative for this alert | Yes |
| `/kpi/alerts/{alert_id}/recommendation` | GET | Structured recommendation (driver→lever→action→owner→monitoring) | Yes |
| `/feedback` | POST | Submit human decision (accept/reject/edit/request_more_evidence) | Yes |
| `/telemetry/summary` | GET | Latency/token/cost summary (analyst/admin only) | Yes |
| `/pipeline/run` | POST | Manually trigger a detection run (demo/testing convenience) | Yes (analyst/admin only) |

## 5. Authorization Rules (endpoint-level, on top of DB-level RLS)
| Rule | Enforcement |
|---|---|
| Regional Leader can only fetch alerts for their own region | Enforced twice: RLS at DB layer (defense in depth) + explicit role check in endpoint |
| Only Analyst/Admin can trigger `/pipeline/run` | Role check in FastAPI dependency |
| Only Analyst/Admin can view `/telemetry/summary` | Role check — cost/usage data is not exposed to business-leader persona |
| `/feedback` writes are always tagged with the submitting user's role | For audit trail in `feedback_log` |

## 6. Sensitive Data Protection
- No raw customer PII (names, emails, phone numbers) included in the synthetic dataset at all —
  `customer_segment` is a category, not an identifier, avoiding the need for PII-specific
  handling in MVP while still demonstrating the security pattern.
- API responses never include raw SQL query text to the frontend — only the `lineage_query_ref`
  identifier (per `05_DATA_SCHEMA.md` §3.3), preventing accidental exposure of internal schema
  details to end users.

## 7. Auditability
Every state-changing action (`/feedback` POST, `/pipeline/run` POST) is logged with
`user_id`, `role`, `timestamp`, and the action taken — stored in `feedback_log` (§3.5 of
`05_DATA_SCHEMA.md`) or a dedicated `audit_log` table if scope allows. This satisfies Round 2's
"row-, column- and domain-level security, sensitive-data protection and auditability" point.

## 8. Rate Limiting & Cost Protection (ties to `09_LLM_ARCHITECTURE.md` §9)
- `/kpi/alerts/{alert_id}/narrative` responses are cached per (alert_id, persona) so repeated
  requests don't trigger repeated LLM calls
- Simple in-memory or Postgres-backed rate limit on `/pipeline/run` to prevent accidental
  runaway LLM cost during demo/testing (e.g. max 1 run per minute)

## 9. What's Explicitly Out of Scope (MVP)
- Multi-tenant org isolation
- Fine-grained per-column GRANTs beyond the two demo views
- Enterprise SSO/OAuth/SAML
- Penetration-tested production hardening
These are named directly so the security design reads as a deliberate MVP scope choice, not an
oversight — consistent with `01_PRD.md` §3 Non-Goals.