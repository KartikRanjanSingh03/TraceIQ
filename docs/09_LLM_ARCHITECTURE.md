# TraceIQ — LLM Architecture

## 1. Purpose
Round 2 explicitly requires: "The LLM should not be treated as the source of quantitative
truth... Teams should explicitly demonstrate when they use deterministic logic, SQL, business
rules, statistics, traditional ML, causal inference, retrieval or LLMs—and why." This doc
defines exactly where the LLM sits, what it receives, what it's forbidden from doing, and how
persona-specific narratives are generated.

## 2. Position in the Pipeline
The LLM is the **second-to-last** stage (before UI rendering), per `02_ARCHITECTURE.md` §3.10.
Everything before it — detection, decomposition, hypotheses, evidence, confidence, ambiguity
gate, recommendation — is fully deterministic and has already run to completion.

## 3. What the LLM Is Explicitly Forbidden From Doing
(Carried directly from the Round 1 blueprint, non-negotiable for this system)
- ❌ Calculating anomalies or z-scores
- ❌ Performing any KPI arithmetic
- ❌ Inventing or adjusting confidence scores/percentages
- ❌ Declaring causality independently of the Evidence/Confidence Engine's output
- ❌ Generating evidence (it only synthesizes evidence already retrieved)
- ❌ Making or executing autonomous business decisions
- ❌ Deciding whether to abstain (that's decided by `08_CONFIDENCE_AND_ABSTENTION.md` before the LLM is called)

## 4. What the LLM Is Responsible For
- ✅ Hypothesis expansion (suggesting additional candidates, non-authoritative — see `07_ANALYTICS_AND_DRIVER_ANALYSIS.md` §4.2)
- ✅ Evidence synthesis into readable prose
- ✅ Executive/business storytelling from structured evidence
- ✅ Persona-specific tone and depth adaptation
- ✅ Communicating uncertainty using the prescribed language policy (Tier 1-4, `07_ANALYTICS_AND_DRIVER_ANALYSIS.md` §7)
- ✅ Recommendation wording (not the recommendation's substance — that comes from the Recommendation Engine)

## 5. Input: The Evidence Package (exact contract)
The LLM never receives raw tables. It receives a single, pre-validated JSON object per alert:
```json
{
  "kpi": "Revenue",
  "change": "-8.2%",
  "significance": "High",
  "affected_segment": "North / Electronics",
  "persona": "business_leader",
  "hypotheses": [
    {
      "driver": "Inventory shortage",
      "confidence": "Strong",
      "supporting_evidence": [
        "Stockouts increased 17% in the affected segment",
        "Inventory availability declined 13%",
        "Warehouse outage incident reported same week"
      ],
      "conflicting_evidence": [],
      "recommendation": {
        "controllable_lever": "Replenishment priority",
        "action": "Prioritize restocking of highest-impact SKUs",
        "expected_impact": "Recover a meaningful share of lost North Electronics orders",
        "owner_role": "Regional Ops Manager",
        "monitoring_kpi": "Inventory Availability -> Orders -> Revenue"
      }
    },
    {
      "driver": "Competitor activity",
      "confidence": "Insufficient",
      "supporting_evidence": ["One ticket mentions a competitor promotion"],
      "conflicting_evidence": ["Affected segment pattern doesn't match typical competitor-driven impact"],
      "missing_data": "Competitor pricing/exposure data for the affected region and period"
    }
  ]
}
```

## 6. System Prompt Contract (behavioral rules given to the LLM)
1. Use only the supplied evidence — do not invent causes, numbers, or evidence.
2. Do not present correlation as proven causation; follow the evidence-tier language policy.
3. Always mention uncertainty and contradictions where they exist in the input.
4. Only phrase a recommendation where the input package includes one — never invent one.
5. Adapt tone/depth to the `persona` field (see §7) without altering any factual content.
6. If a hypothesis has `confidence: "Insufficient"`, state that plainly and name the missing
   data — do not soften this into a implied conclusion.

## 7. Persona Adaptation (MVP: 2 personas, per `01_PRD.md` §5)
| Persona | Prompt instruction | Output shape |
|---|---|---|
| Business Leader | "Lead with impact and action. Minimal jargon. 3-5 sentences max." | Short narrative + one clear recommendation |
| Analyst | "Include full evidence trail, method names, and confidence reasoning." | Longer narrative with evidence citations and methodology notes |

Same underlying evidence JSON is used for both — only the prompt instructions and rendering
differ. This ensures personas never see factually different conclusions, only differently
framed ones (audit-safe).

## 8. Model Choice (per `03_TECH_STACK.md`, zero-budget constraint)
- Default: smallest/cheapest capable model (e.g. Claude Haiku or Gemini free-tier equivalent) —
  narrative synthesis from a small, structured JSON input does not require a large/expensive
  reasoning model.
- Model is swappable behind a single interface (`llm_client.py` or equivalent) so switching
  providers requires no change to prompts, evidence contract, or downstream code.

## 9. Cost & Latency Control
- Evidence package kept minimal (only what's needed for the narrative) → low input token count
- One LLM call per persona per alert (not per hypothesis) → bounded call volume
- Response caching: identical evidence package + persona = cached response, to avoid redundant
  calls during demo/testing
- All calls logged to `telemetry_log` (per `05_DATA_SCHEMA.md` §3.6) — latency, tokens, cost

## 10. Hallucination Guardrail (testable)
Directly implements the Round 1 "Hallucination test": if asked about a factor with zero
supporting evidence in the input package (e.g. pricing, when no pricing signal exists), the
LLM must state that no evidence supports that explanation — verified in
`15_TESTING_STRATEGY.md` as a required automated test case.