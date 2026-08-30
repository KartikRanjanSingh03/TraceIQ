**# TraceIQ — Backend Module Plan**

**## 1. Purpose**

Maps the architecture layers in \`02\_ARCHITECTURE.md\` to concrete Python modules/files, so the

build order in \`17\_ROADMAP.md\` has a 1:1 code structure to follow. This is the bridge between

"design docs" and "actual repo layout."

**## 2. Repository Structure**

\`\`\`

traceiq/

├── docs/                          # this documentation set

├── data/

│   ├── generate\_synthetic\_data.py # creates the MVP dataset

│   └── seed/                      # generated CSVs before DB load

├── db/

│   ├── migrations/                # SQL migration files (10\_DATABASE\_DESIGN.md §8)

│   └── connection.py              # Postgres connection pool

├── src/

│   ├── semantic/

│   │   └── kpi\_contract.py        # loads/validates kpi\_contract.yaml (04\_KPI\_SEMANTIC\_CONTRACT.md)

│   ├── detection/

│   │   └── change\_detection.py    # baseline, z-score, persistence, materiality (07 §2)

│   ├── decomposition/

│   │   └── kpi\_decomposition.py   # formula + dimensional contribution (07 §3)

│   ├── hypothesis/

│   │   ├── rules.py                # rule-based hypothesis generation (07 §4.1)

│   │   └── llm\_suggest.py          # LLM-assisted candidate suggestion (07 §4.2)

│   ├── evidence/

│   │   ├── structured\_evidence.py  # SQL evidence queries (07 §5.1)

│   │   └── retrieval.py            # embedding + Chroma semantic search (07 §5.2)

│   ├── confidence/

│   │   └── scoring.py              # weighted confidence engine (07 §6)

│   ├── ambiguity/

│   │   └── gate.py                 # High/Moderate/Insufficient routing (08)

│   ├── recommendation/

│   │   └── recommendation\_engine.py # driver→lever→action→owner→monitoring (02 §3.9)

│   ├── llm/

│   │   ├── llm\_client.py           # provider-agnostic interface (Claude/Gemini) (09 §8)

│   │   ├── prompts.py              # system prompt + evidence-package templates (09 §5-6)

│   │   └── narrative.py            # persona-adapted narrative generation (09 §7)

│   ├── telemetry/

│   │   └── logger.py               # latency/token/cost logging (05 §3.6)

│   └── pipeline/

│       └── orchestrator.py         # runs the full Data→...→Recommendation chain in order

├── api/

│   ├── main.py                     # FastAPI app entrypoint

│   ├── auth.py                     # JWT issuance/validation (11 §2-3)

│   ├── routes/

│   │   ├── alerts.py

│   │   ├── feedback.py

│   │   └── telemetry.py

│   └── deps.py                     # role/region dependency injection for RLS session var

├── ui/

│   └── app.py                      # Streamlit Investigation Canvas (12)

├── tests/

│   └── (mirrors src/ structure, per 15\_TESTING\_STRATEGY.md)

├── requirements.txt

├── .env.example

└── README.md

\`\`\`

**## 3. Module Responsibilities (recap, 1:1 with architecture)**

\| Module | Architecture layer | Deterministic or LLM |

\|---|---|---|

\| \`semantic/kpi\_contract.py\` | KPI Semantic Layer | Deterministic |

\| \`detection/change\_detection.py\` | Change Detection | Deterministic |

\| \`decomposition/kpi\_decomposition.py\` | KPI Decomposition | Deterministic |

\| \`hypothesis/rules.py\` | Hypothesis Engine (primary) | Deterministic |

\| \`hypothesis/llm\_suggest.py\` | Hypothesis Engine (secondary) | LLM (non-authoritative) |

\| \`evidence/structured\_evidence.py\` | Evidence Engine (structured) | Deterministic (SQL) |

\| \`evidence/retrieval.py\` | Evidence Engine (unstructured) | Deterministic pipeline, embedding model (not generative LLM) |

\| \`confidence/scoring.py\` | Validation + Confidence Engine | Deterministic |

\| \`ambiguity/gate.py\` | Ambiguity Gate | Deterministic |

\| \`recommendation/recommendation\_engine.py\` | Recommendation Engine | Deterministic |

\| \`llm/narrative.py\` | LLM Layer | LLM |

\| \`pipeline/orchestrator.py\` | Orchestrates all of the above in fixed order | N/A (control flow) |

**## 4. Orchestrator Contract (the pipeline's backbone)**

\`pipeline/orchestrator.py\` runs, per KPI per scheduled tick:

\`\`\`

alert = change\_detection.run(kpi, date)

if alert.status != "INVESTIGATE": return alert  # stop early, no wasted work

decomposition = kpi\_decomposition.run(alert)

candidates = rules.generate(alert, decomposition)

\# Optional LLM-assisted candidate expansion — core system must work without this

if config.enable\_llm\_hypothesis\_suggestions:

    candidates += llm\_suggest.generate(alert, decomposition)

for hyp in candidates:

    hyp.evidence = structured\_evidence.gather(hyp) + retrieval.gather(hyp)

    hyp.confidence = scoring.evaluate(hyp)

gated = gate.route(candidates)          # High / Moderate / Insufficient per hypothesis

for hyp in gated:

    if hyp.confidence == "Strong":

        hyp.recommendation = recommendation\_engine.build(hyp)

    elif hyp.confidence == "Moderate":

        hyp.recommendation = recommendation\_engine.build\_conditional(hyp)  # flagged for human review, per 08 §3.2

    else:

        hyp.recommendation = None  # Weak/Insufficient — no recommendation, abstention path per 08

evidence\_package = build\_llm\_package(alert, decomposition, gated)  # 09 §5 contract

narrative\_leader  = narrative.generate(evidence\_package, persona="business\_leader")

narrative\_analyst = narrative.generate(evidence\_package, persona="analyst")

telemetry.log(...)  # every LLM call logged here

persist(alert, gated, narrative\_leader, narrative\_analyst)

\`\`\`

This function is the single place that enforces "no evidence → no confident story" — nothing

downstream of it can add unvalidated claims.

**## 5. Scheduling**

\`pipeline/orchestrator.py\` triggered by:

\- A simple scheduled job (Python \`schedule\` lib or cron, per \`03\_TECH\_STACK.md\`) simulating

  daily batch runs, AND

\- The manual \`/pipeline/run\` API endpoint (per \`11\_SECURITY\_AND\_API.md\` §4) for demo control

**## 6. Provider-Agnostic LLM Client (satisfies zero-budget flexibility)**

\`llm/llm\_client.py\` exposes one function: \`generate(prompt, evidence\_package) -> text\`.

Internally it dispatches to Claude or Gemini based on config/env var — no other module ever

imports a provider SDK directly, so switching providers touches one file only.