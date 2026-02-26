---
name: tableau-dashboard-creator
description: Guides data analysts through a multi-step Tableau dashboard creation workflow - brand setup, data exploration, dashboard planning, interactive HTML mock creation, Tableau implementation spec, and experimental TWB workbook generation. Use when the user wants to create a Tableau dashboard, build a dashboard mock, plan KPIs and charts, or generate a Tableau implementation document.
disable-model-invocation: true
---

# Tableau Dashboard Creator

A multi-step workflow that transforms a human-language dashboard request into an implementable Tableau specification with an interactive HTML mock and an optional generated workbook.

## Prerequisites

Before starting, verify the user has provided:

```
Checklist:
- [ ] QUERIES.md — SQL queries grouped under database type headings (Databricks / PostgreSQL / Snowflake)
      OR sample-data/ directory with CSV, JSON, or XLSX files
- [ ] <DASHBOARD-NAME>-PDR.md — Human-language dashboard request
- [ ] .env — Database credentials (skip if using sample-data/)
- [ ] ONE of the following for branding:
      - template.twb — Organization's Tableau template workbook (in project root)
      - branding/ directory containing: logo (PNG/SVG) + palette (PDF or JSON)
```

If any are missing, ask the user to provide them before proceeding.

### QUERIES.md Format

Queries must be grouped under a heading matching the database type:

```markdown
## Databricks
SELECT * FROM catalog.schema.table1

## PostgreSQL
SELECT * FROM public.table2
```

The agent selects the correct query script based on the heading.

### Required Python packages (per database type)

```
# Databricks
databricks-sql-connector, python-dotenv, pandas

# PostgreSQL
psycopg2-binary, python-dotenv, pandas

# Snowflake
snowflake-connector-python, python-dotenv, pandas
```

## Workflow Overview

```
Step 0: Brand Setup ──[user approval]──> Step A: Data Exploration
Step A: Data Exploration ──[user approval]──> Step B: Dashboard Planning
Step B: Dashboard Planning ──[user approval]──> Step C: HTML Mock
Step C: HTML Mock ──[user approval]──> Step D: Implementation Spec
Step D: Implementation Spec ──[user approval]──> Step E: TWB Generation (Experimental)
```

**Do NOT skip steps. Wait for explicit user approval before moving to the next step.**

## Step 0: Brand Setup

**Identity**: Tableau design systems engineer.

Read [references/step-0-brand-setup.md](references/step-0-brand-setup.md) for detailed instructions.

Summary:
1. Detect branding source: `template.twb` in project root OR `branding/` directory
2. Extract design tokens from `.twb` XML, or build them from logo + palette
3. Generate `design-tokens.md` in the project root
4. Present design-tokens.md to the user for approval

## Step A: Data Exploration

**Identity**: Senior Data Engineer.

Read [references/step-a-data-exploration.md](references/step-a-data-exploration.md) for detailed instructions.

Summary:
1. **Check for local data first**: If `sample-data/` directory exists with CSV/JSON/XLSX files, scan them directly — skip database queries
2. **Otherwise**, read QUERIES.md, identify the database type from headings, and execute each query via the matching script:
   - Databricks: `scripts/query_databricks.py`
   - PostgreSQL: `scripts/query_postgresql.py`
   - Snowflake: `scripts/query_snowflake.py`
3. Analyze schema and sample data
4. Create `DS-ARCHITECTURE.md` with datasource and field descriptions
5. Present DS-ARCHITECTURE.md to the user for approval

## Step B: Dashboard Planning

**Identity**: Senior Data Analyst specialized in informative graphs and dashboards.

Read [references/step-b-dashboard-planning.md](references/step-b-dashboard-planning.md) for detailed instructions.

Summary:
1. Read the PDR file and DS-ARCHITECTURE.md
2. Identify KPIs and chart types from the user request
3. Map each visualization to specific columns from DS-ARCHITECTURE.md
4. Suggest additional KPIs and interesting data patterns
5. Propose filter strategy and dashboard actions
6. Present the dashboard plan to the user for approval

## Step C: HTML Mock Creation

**Identity**: Tableau Developer.

Read [references/step-c-mock-creation.md](references/step-c-mock-creation.md) for detailed instructions.

Summary:
1. Read `design-tokens.md` (generated in Step 0)
2. Select appropriate template layout based on design tokens
3. Create interactive HTML mock with Chart.js and sample data
4. Save to `mock-version/v_N/mock.html`
5. Present mock to the user for approval

## Step D: Tableau Implementation Spec

Read [references/step-d-implementation-spec.md](references/step-d-implementation-spec.md) for detailed instructions.

Summary:
1. Translate the approved HTML mock into a technical Tableau implementation spec
2. Document container hierarchy, sheets, calculated fields, parameters
3. Save to `mock-version/v_N/TABLEAU-IMPLEMENTATION.md`

## Step E: TWB Workbook Generation (Experimental)

Read [references/step-e-twb-generation.md](references/step-e-twb-generation.md) for detailed instructions.

Summary:
1. Read the approved TABLEAU-IMPLEMENTATION.md and design-tokens.md
2. Generate a valid `.twb` XML workbook implementing the full dashboard spec (datasource paths must use `directory='.'`)
3. Use sample data CSVs from Step A as the datasource
4. Package as `.twbx` (ZIP archive bundling the `.twb` + all CSV data files) to eliminate path issues
5. Save both `dashboard.twb` and `dashboard.twbx` to `mock-version/v_N/`
6. Instruct the user to open **`dashboard.twbx`** in Tableau Desktop and use **Data → Replace Data Source** to connect live data

## Version Management

- All mock and implementation files go inside `mock-version/v_N/` (e.g., `mock-version/v_1/`)
- Each version is a **full standalone copy** (mock.html + TABLEAU-IMPLEMENTATION.md + dashboard.twb + dashboard.twbx)
- When the user requests revisions after step C, D, or E, increment the version number
- DS-ARCHITECTURE.md, DASHBOARD-PLAN.md, and design-tokens.md live at the project root (shared across versions)

## File Structure (per project)

```
project-root/
├── QUERIES.md                      (user input — with DB type headings)
├── <DASHBOARD-NAME>-PDR.md         (user input)
├── .env                            (user input — DB credentials)
├── template.twb                    (user input — option A: org template)
├── branding/                       (user input — option B: logo + palette)
│   ├── logo.png / logo.svg
│   └── palette.pdf / palette.json
├── sample-data/                    (user input — optional, skip DB queries)
│   └── *.csv / *.json / *.xlsx
├── design-tokens.md                (generated - step 0)
├── DS-ARCHITECTURE.md              (generated - step A)
├── DASHBOARD-PLAN.md               (generated - step B)
└── mock-version/
    ├── v_1/
    │   ├── mock.html               (generated - step C)
    │   ├── TABLEAU-IMPLEMENTATION.md (generated - step D)
    │   ├── dashboard.twb           (generated - step E, raw XML)
    │   └── dashboard.twbx          (generated - step E, packaged with data)
    └── v_2/
        ├── mock.html
        ├── TABLEAU-IMPLEMENTATION.md
        ├── dashboard.twb
        └── dashboard.twbx
```