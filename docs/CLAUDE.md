# CLAUDE.md — Agent Instructions for Building TraceIQ

This file is read first by any AI coding agent (Claude, Gemini, Antigravity, etc.) working on
this repository. It is the binding contract for how code gets written here.

## 1. Before Doing Anything
1. Read `00_IMPLEMENTATION_PROGRESS.md` — find the first unchecked task. That is the only task
   to work on right now. Do not skip ahead or work on multiple stages at once.
2. Read `17_ROADMAP.md` to see which stage that task belongs to and what test it must pass.
3. Read the specific design doc(s) referenced for that stage before writing any code — don't
   invent design decisions that are already made in `docs/`.

## 2. Non-Negotiable Product Rules
These come directly from `01_PRD.md` and `09_LLM_ARCHITECTURE.md` and must never be violated
by any code change, regardless of what a task seems to ask for:
- **NO EVIDENCE → NO CONFIDENT STORY.** Nothing downstream may output a factual claim that is not supported by the validated evidence package produced upstream in the pipeline.
- The LLM never computes anomalies, KPI math, confidence scores, or causality. It only
  synthesizes language from a pre-validated evidence JSON package (`09_LLM_ARCHITECTURE.md` §5).
- Confidence labels are qualitative (Strong/Moderate/Weak/Insufficient), computed by
  deterministic weighted scoring (`07_ANALYTICS_AND_DRIVER_ANALYSIS.md` §6) — never an
  LLM-invented percentage.
- When evidence is insufficient or contradictory, the system must abstain and state what's
  missing (`08_CONFIDENCE_AND_ABSTENTION.md`) — never force a confident-sounding answer.
- Row/column-level security is enforced at the database layer (RLS on base tables, per
  `10_DATABASE_DESIGN.md` §5), not only in application code.

## 3. Build Order Discipline
- One stage from `17_ROADMAP.md` at a time. Do not build the LLM layer or UI before the
  deterministic pipeline (detection → decomposition → hypothesis → evidence → confidence →
  ambiguity → recommendation) is complete and tested.
- A stage is not "done" until its listed test in `15_TESTING_STRATEGY.md` passes.
- After completing a stage: update `00_IMPLEMENTATION_PROGRESS.md` (check the box, add a
  one-line status note), then stop and report back — don't auto-continue into the next stage
  without confirmation, unless explicitly told to run continuously.

## 4. Code Conventions
- Python 3.11+, type hints on all function signatures.
- One module = one responsibility, matching `13_BACKEND_MODULE_PLAN.md` §2 exactly. Don't
  create new top-level modules without updating that doc first.
- No hardcoded KPI formulas, thresholds, or business rules inside application code — they must
  come from `kpi_contract.yaml` / `semantic/kpi_contract.py` (`04_KPI_SEMANTIC_CONTRACT.md` §8).
- No provider-specific LLM SDK calls outside `llm/llm_client.py` — all LLM access goes through
  that single interface so switching Claude/Gemini touches one file.
- Every module gets unit tests in `tests/` (mirroring `src/` structure) before being considered
  complete — write tests alongside the code, not after.
- Use environment variables (`.env`, never committed) for all secrets/keys — see
  `16_DEPLOYMENT_AND_TELEMETRY.md` §3 for the exact variable names expected.

## 5. Zero-Budget Constraint
Default to free/self-hosted tools per `03_TECH_STACK.md`. If a task seems to require a paid
service, flag it and propose the free-tier or self-hosted alternative instead of silently
using a paid one.

## 6. When Docs and a Request Conflict
If a user request conflicts with something already decided in `docs/`, say so explicitly and
ask whether to update the doc or follow the existing decision — don't silently override a
documented design decision, and don't silently ignore the request either.

## 7. Testing Discipline
Every scenario in `15_TESTING_STRATEGY.md` §4 (Noise, True Anomaly, Root-Driver, Contradiction,
Ambiguity, Hallucination, Recommendation Relevance, Sparse-History, Role-Based Security,
Persona Narrative, Feedback Loop) must pass before the project is considered submission-ready
(Stage 15 in `17_ROADMAP.md`). These are not optional nice-to-haves — they are the direct
proof that the system satisfies the Round 2 brief's minimum expectations.

## 8. What NOT to Do
- Don't add frameworks/infra not listed in `03_TECH_STACK.md` §2 without updating that doc first.
- Don't build multi-agent orchestration, knowledge graphs, or streaming infra — explicitly out
  of scope (`03_TECH_STACK.md` §3).
- Don't let the LLM decide whether to abstain — that decision is made by `ambiguity/gate.py`
  before the LLM is ever called.
- Don't skip writing tests to "move faster" — untested stages compound into unfixable bugs
  later, which is the primary failure mode this staged approach is designed to prevent.