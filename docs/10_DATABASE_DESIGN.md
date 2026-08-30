**# TraceIQ — Database Design**

**## 1. Purpose**

\`05\_DATA\_SCHEMA.md\` defines table columns and relationships. This doc covers physical

database implementation: engine choice, indexing, views, and how row/column-level security

(from \`04\_KPI\_SEMANTIC\_CONTRACT.md\` §7) is actually enforced at the database layer.

**## 2. Engine**

PostgreSQL 15+ (per \`03\_TECH\_STACK.md\`). Chosen over a plain CSV/pandas-only approach because:

\- Real SQL semantics needed for the Evidence Engine's structured queries

\- Native support for row-level security (RLS) policies — directly usable for §5 below

\- Views can encode the KPI Semantic Contract without duplicating logic in application code

\- Free to self-host locally (Docker), meets zero-budget constraint

**## 3. Schema Organization**

\`\`\`

schema: raw        -- source-aligned tables (orders, inventory\_snapshots, deliveries, tickets\_notes)

schema: kpi         -- semantic views (kpi\_daily\_revenue, kpi\_daily\_orders, etc.)

schema: pipeline     -- system tables (kpi\_alerts, hypotheses, recommendations, feedback\_log, telemetry\_log)

\`\`\`

Separating \`raw\` from \`kpi\` views keeps the KPI Semantic Contract (\`04\_KPI\_SEMANTIC\_CONTRACT.md\`)

enforced at the database level — no application code computes a KPI formula independently.

**## 4. Key Indexes**

\| Table | Index | Reason |

\|---|---|---|

\| \`orders\` | (date, region, category, product\_id) | Primary grouping path for KPI aggregation |

\| \`orders\` | (order\_id) | Join support to deliveries (logical FK, see \`05\_DATA\_SCHEMA.md\` §4) |

\| \`inventory\_snapshots\` | (date, region, category, product\_id) | Same grouping path, joins to orders |

\| \`deliveries\` | (dispatch\_timestamp, region) | Daily SLA aggregation |

\| \`tickets\_notes\` | (timestamp) | Time-window filtering before semantic retrieval narrows candidates |

\| \`kpi\_alerts\` | (kpi\_name, date, status) | Fast lookup of active INVESTIGATE alerts |

\| \`hypotheses\` | (alert\_id) | Join to parent alert |

**## 5. Row-Level Security (implements \`04\_KPI\_SEMANTIC\_CONTRACT.md\` §7)**

**\*\*Critical implementation detail\*\***: \`kpi.kpi\_daily\_revenue\` is a VIEW (see §7), not a table —

RLS policies cannot be enabled on a view directly. RLS must be enforced on the underlying base

table (\`raw\.orders\`), and the view must be marked \`security\_invoker\` (or \`security\_barrier\` in

older Postgres) so it respects the caller's row-level restrictions instead of running with the

view owner's full privileges.

\`\`\`

raw\.orders  (RLS enabled here — the real enforcement point)

   │  region = current\_setting('app.current\_user\_region')  [for regional\_leader\_role]

   │  true  [for analyst\_role, sees all regions]

   ▼

kpi.kpi\_daily\_revenue  (view, WITH (security\_invoker = true), inherits base-table RLS)

   ▼

FastAPI  (sets app.current\_user\_region per session from auth — see §6 and 11\_SECURITY\_AND\_API.md)

   ▼

UI  (renders only what the query returned)

\`\`\`

\`\`\`sql

ALTER TABLE raw\.orders ENABLE ROW LEVEL SECURITY;

CREATE POLICY regional\_leader\_policy

ON raw\.orders

FOR SELECT

TO regional\_leader\_role

USING (region = current\_setting('app.current\_user\_region'));

CREATE POLICY analyst\_policy

ON raw\.orders

FOR SELECT

TO analyst\_role

USING (true);

\-- View must run as the querying user, not the view owner, to respect the above:

ALTER VIEW kpi.kpi\_daily\_revenue SET (security\_invoker = true);

\`\`\`

The application sets \`app.current\_user\_region\` per session based on the logged-in user's

profile (see \`11\_SECURITY\_AND\_API.md\` for how this is populated from auth).

**## 6. Column-Level Security**

Enforced via separate views rather than Postgres column-level GRANTs (simpler for MVP):

\`\`\`sql

CREATE VIEW kpi.revenue\_regional\_leader\_view AS

SELECT date, region, category, revenue, order\_count, aov

FROM kpi.kpi\_daily\_revenue;

\-- no raw customer\_segment-level or per-order detail exposed

CREATE VIEW kpi.revenue\_analyst\_view AS

SELECT \* FROM kpi.kpi\_daily\_revenue;

\-- full detail including evidence-supporting raw fields

\`\`\`

Both views must also be \`security\_invoker = true\` so they inherit the base-table RLS from §5

rather than bypassing it. Backend API (\`11\_SECURITY\_AND\_API.md\`) routes queries to the correct

view based on role.

**### 6.1 Combined Row + Column Security per Role (full picture)**

\| Role | View used (column-level) | RLS policy applied (row-level, from §5) | Net result |

\|---|---|---|---|

\| Regional Leader | \`kpi.revenue\_regional\_leader\_view\` | \`regional\_leader\_policy\` → own region only | Sees KPI columns only, restricted to their region |

\| Analyst | \`kpi.revenue\_analyst\_view\` | \`analyst\_policy\` → all regions | Sees full detail, across all regions |

This table is the single reference for \`04\_KPI\_SEMANTIC\_CONTRACT.md\` §7's access-restriction

requirement — column restriction (which view) and row restriction (which RLS policy) are

applied together, not independently, so a role's actual visible data is the intersection of both.

**## 7. Sample KPI View (implements \`04\_KPI\_SEMANTIC\_CONTRACT.md\` §3.1 formula)**

\`\`\`sql

CREATE VIEW kpi.kpi\_daily\_revenue AS

SELECT

  date, region, category, product\_id, channel, customer\_segment,

  SUM(revenue) AS revenue,

  COUNT(DISTINCT order\_id) AS order\_count,

  SUM(revenue) / NULLIF(COUNT(DISTINCT order\_id), 0) AS aov

FROM raw\.orders

GROUP BY date, region, category, product\_id, channel, customer\_segment;

\`\`\`

**## 8. Migrations**

Simple, versioned SQL migration files (\`001\_init\_raw\_tables.sql\`,

\`002\_create\_kpi\_views.sql\`, \`003\_pipeline\_tables.sql\`, \`004\_rls\_policies.sql\`) run via a

lightweight tool (Alembic or plain scripted \`psql\` execution) — no heavyweight migration

framework needed for MVP scale.

**## 9. Backup / Persistence (MVP-level)**

Local Docker volume for Postgres data persistence during development. No production-grade

backup/replication needed for a prototype — explicitly out of scope, consistent with

\`01\_PRD.md\` §3 Non-Goals.

**## 10. Scalability Note**

At MVP scale (thousands to low-millions of rows across synthetic sources), Postgres with the

indexes in §4 comfortably serves all query patterns used by the pipeline. If scaled to real

production volume, the same view-based KPI contract and RLS policies map directly onto a

columnar warehouse (Snowflake/BigQuery) — the logical design doesn't change, only the engine.