# TraceIQ — Frontend / UX Plan

## 1. Purpose
Defines the "Investigation Canvas" UI — not a conventional dashboard, but a workspace that
shows the KPI movement, the investigation trail, the evidence, and lets a human act on it.
Directly implements the Round 1 blueprint's UI vision and satisfies Round 2's persona,
security, and evidence-display requirements.

## 2. Tech Choice
Streamlit for MVP (per `03_TECH_STACK.md`) — fastest path to a working, interactive UI without
a separate frontend build/deploy step. React+FastAPI only if time remains post-MVP.

## 3. Screen Structure

### 3.1 Login / Persona Selector
Simple login (per `11_SECURITY_AND_API.md` §2) that sets the session's role/region. For demo
convenience, a persona switcher lets the presenter show both Regional Leader and Analyst views
without logging out/in repeatedly (dev/demo-only affordance, clearly labeled as such).

### 3.2 KPI Alert List (landing view)
List of current alerts (from `/kpi/alerts`, already RLS-filtered), each showing: KPI name,
% movement, status badge (WATCH/INVESTIGATE), affected segment, and confidence summary badge.
Sorted by business materiality (highest impact first).

### 3.3 Investigation Canvas (detail view, per alert)
**Section 1 — KPI Alert Header**
"Revenue ↓ 8.2% — SIGNIFICANT CHANGE" + expected-vs-actual line chart (Plotly), with the
statistical/materiality thresholds shown so the "why this triggered" is transparent.

**Section 2 — Decomposition Tree**
Visual breakdown: Revenue ↓8.2% → Orders ↓11.4% → North → Electronics — implemented as a
simple indented tree or Plotly treemap, sourced from
`07_ANALYTICS_AND_DRIVER_ANALYSIS.md` §3.

**Section 3 — Evidence Graph**
Visual chain from KPI → decomposed segment → supporting signals → source documents. Clicking
a node opens "Why do you believe this?" showing the underlying evidence object (per
`05_DATA_SCHEMA.md` §3.3: description, source, method, contribution, freshness, lineage).

**Section 4 — Hypothesis Panel**
Each hypothesis shown as a card: driver name, confidence badge (🟢Strong/🟡Moderate/
🟠Weak/🔴Insufficient), a checklist of supporting evidence (✓), contradicting evidence (⚠/✕),
and — for Insufficient hypotheses — the explicit missing-data statement from
`08_CONFIDENCE_AND_ABSTENTION.md` §4.

**Section 5 — Narrative Panel**
The LLM-generated, persona-adapted explanation (from `/kpi/alerts/{id}/narrative`), rendered
as readable prose. A small "LLM vs deterministic" indicator/tooltip clarifies this text is
generated language over pre-validated facts — not a new analysis.

**Section 6 — Action Card**
Driver → Evidence summary → Recommendation → Monitoring KPI, with buttons: Accept / Reject /
Need More Evidence / Investigate Further — posts to `/feedback`.

**Section 7 — Telemetry Footer (Analyst/Admin only)**
Latency, LLM calls made, tokens used, estimated cost for this alert's processing — pulled from
`/telemetry/summary`, hidden from Business Leader persona per `11_SECURITY_AND_API.md` §5.

## 4. Persona-Differentiated Rendering
| Element | Business Leader view | Analyst view |
|---|---|---|
| Narrative | Short, action-first | Full evidence trail, method names |
| Hypothesis Panel | Top hypothesis only, simplified | All hypotheses, full scoring detail |
| Evidence Graph | Collapsed by default | Expanded by default |
| Telemetry Footer | Hidden | Visible |
| Region scope | Own region only (RLS-enforced) | All regions, selectable |

**Important**: "simplified" never means hiding uncertainty. If the top hypothesis (or the
overall alert) is 🟡 Moderate or 🔴 Insufficient, the Business Leader view must still show that
status clearly (e.g. a visible confidence badge and a one-line "insufficient evidence" note) —
only the depth of evidence detail is reduced, never the honesty of the confidence level. This
is required by `08_CONFIDENCE_AND_ABSTENTION.md`, which applies uniformly to all personas.

**Persona switcher clarification**: the demo/dev persona switcher described in §3.1 is a
**presentation convenience only** — it does not bypass or represent the actual security
mechanism. Real access control is enforced server-side via JWT role/region claims and database
RLS (`11_SECURITY_AND_API.md` §3). The switcher exists purely so a presenter can show both
views in one demo session without re-logging in; it must be clearly labeled as a demo-only
control and would be removed (replaced by normal login-based role assignment) outside the
demo build.

## 5. Demonstrating Required MVP Scenarios in the UI
| Requirement | How it's shown |
|---|---|
| Multi-factor movement | Decomposition tree + multiple hypothesis cards on one alert |
| Low-confidence/abstention | A hypothesis card rendered in 🔴 Insufficient state with missing-data callout |
| Sparse-history KPI | A distinct alert badge: "Limited history — category-level baseline used" |
| Role-based security | Switching persona visibly changes visible regions/detail (§4) |
| LLM vs non-LLM breakdown | Explicit tooltip/label distinguishing narrative (LLM) from evidence/scores (deterministic) |
| Evidence: freshness/method/contribution/confidence/lineage | All five fields visible on evidence click-through (Section 3) |
| Telemetry | Section 7 footer |

## 6. Visual Style
Follows `frontend-design` skill guidance where applicable (even in Streamlit): clear
information hierarchy, status color-coding (green/amber/red consistently used for
confidence/status throughout, not just in one section), no unnecessary decoration — the UI's
job is legibility and trust, not flash.

## 7. Out of Scope for MVP UI
- Mobile-responsive layout
- Real-time push updates (UI refreshes on demand / page reload, not via websockets)
- Full accessibility audit (basic semantic structure only)
These are named explicitly as deliberate MVP scope decisions.
