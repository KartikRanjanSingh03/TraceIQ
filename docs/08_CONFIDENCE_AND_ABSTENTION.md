# TraceIQ — Confidence & Abstention

## 1. Purpose
Round 2 explicitly requires: "communicates uncertainty and abstains when evidence is
insufficient or contradictory," and a mandatory prototype scenario: "one low-confidence
scenario in which the engine requests clarification or abstains." This doc defines exactly
how TraceIQ decides when to speak confidently, when to hedge, and when to stop and ask for
more data — the "Ambiguity Gate" from `02_ARCHITECTURE.md` §3.8.

## 2. Relationship to Evidence Validation
This layer consumes the confidence labels already computed in
`07_ANALYTICS_AND_DRIVER_ANALYSIS.md` §6 (Strong/Moderate/Weak/Insufficient per hypothesis).
It does not recompute confidence — it routes each hypothesis (and the overall alert) to an
action based on that label.

## 3. The Three Gate States

### 3.1 🟢 High Confidence
**Trigger**: top-ranked hypothesis is `Strong`, with no unresolved contradicting evidence.
**Behavior**: system proceeds to generate a full recommendation (see
`Recommendation Engine, 02_ARCHITECTURE.md §3.9`) and presents it as the primary driver.
**Human role**: reviews and accepts/rejects — not required to investigate further.

### 3.2 🟡 Moderate Confidence
**Trigger**: top hypothesis is `Moderate`, or two hypotheses are close in score, or some
evidence dimensions pass while others are inconclusive.
**Behavior**: system presents the hypothesis as a "likely contributor" (Tier 2 language, per
`07_ANALYTICS_AND_DRIVER_ANALYSIS.md` §7), with a *conditional* recommendation, and explicitly
flags it for human review before action.
**Human role**: expected to review evidence before accepting.

### 3.3 🔴 Insufficient Evidence (Abstention)
**Trigger**: no hypothesis reaches `Moderate` or higher, OR the top hypotheses are too close
to distinguish, OR sparse-history caps confidence (per `07_ANALYTICS_AND_DRIVER_ANALYSIS.md`
§2.6), OR contradicting evidence outweighs supporting evidence.
**Behavior**: system does **not** name a driver with false confidence. Instead it:
1. States plainly: "Insufficient evidence to determine the cause."
2. Lists the remaining plausible hypotheses it could not rule in or out.
3. Names the **specific missing data** that would resolve the ambiguity.
4. Proposes a concrete next investigation step.
**Human role**: supplies the missing data/context, or manually investigates further.

## 4. Abstention Output Template
```
Status: Insufficient Evidence
Plausible hypotheses: [Competitor activity, Pricing effect]
Why undetermined: Both hypotheses show partial alignment with the affected segment,
                   but neither has independent confirming evidence, and their signals
                   overlap in time.
Missing data needed: Competitor pricing/promotion exposure data for North region,
                      Electronics category, for the affected date range.
Next step: Request competitor intelligence feed access, or manually check
           regional sales team notes for competitor activity reports.
```
This directly implements the Round 1 doc's example: *"Current evidence cannot reliably
distinguish pricing effects from competitor activity..."*

## 5. Worked MVP Abstention Scenario (satisfies Round 2 minimum expectation)
Per `01_PRD.md` §6: the competitor-activity hypothesis in the Revenue↓8.2% scenario is
deliberately designed to land in 🔴 Insufficient Evidence:
- Supporting: one ticket mentions a competitor promotion
- Contradicting: the affected segment pattern (North Electronics specifically) doesn't match
  a typical competitor-driven pattern (which would usually show broader category impact)
- Result: confidence label = `Insufficient`, system abstains on this hypothesis specifically,
  even while confidently naming Inventory shortage as the primary driver for the same alert.

This demonstrates the system can hold **mixed confidence across hypotheses within the same
alert** — not just a single global "confident" or "not confident" flag.

## 6. Contradiction Handling
When evidence directly contradicts a hypothesis (e.g. a comparable, unaffected region shows
the same driver signal without a corresponding KPI drop), this is:
- Recorded explicitly in `hypotheses.conflicting_evidence` (per `05_DATA_SCHEMA.md` §3.2)
- Displayed in the UI Hypothesis Panel alongside supporting evidence (per
  `12_FRONTEND_UX_PLAN.md`), never hidden or silently down-weighted only
- Weighted into the confidence score as a penalty (per `07_ANALYTICS_AND_DRIVER_ANALYSIS.md`
  §6.1), which can pull a hypothesis down from Moderate to Weak/Insufficient

## 7. What the LLM Is and Isn't Allowed to Do Here
- The LLM does **not** decide whether to abstain — that decision is made deterministically by
  this layer before the LLM is ever called.
- The LLM's only job (when the gate state is 🔴) is to phrase the abstention message
  naturally, using the exact evidence/missing-data list provided — it cannot add, remove, or
  soften any part of the abstention.
- If the LLM is asked a question with no supporting evidence in its input package at all
  (e.g. "Was pricing responsible?" with no pricing signal present), it must respond that no
  evidence supports that claim — never invent a plausible-sounding cause (see the Round 1
  "Hallucination test" — a required test case, carried into `15_TESTING_STRATEGY.md`).

## 8. Calibration Note (honesty about MVP limits)
The weights/thresholds behind Strong/Moderate/Weak/Insufficient are configured, reasoned
defaults for MVP — not statistically calibrated against large-scale historical outcomes. This
is disclosed transparently (in the README and demo) rather than presented as a precision-
calibrated system. The Feedback Loop (`14_FEEDBACK_AND_LEARNING_LOOP.md`) is the mechanism by
which these weights would be refined over time in a production setting.