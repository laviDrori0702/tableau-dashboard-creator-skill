# Tableau Dashboard Creator — Claude Code Skill

A Claude Code skill that transforms a human-language dashboard request into an implementable Tableau specification, complete with interactive HTML mock, technical implementation blueprint, and an experimental `.twbx` workbook — all through a guided, approval-gated workflow.

## What This Skill Does

The skill guides you through **6 steps**, each requiring your approval before proceeding:

```
Step 0: Brand Setup         → Extracts design tokens from your branding/template
Step A: Data Exploration    → Analyzes your data sources, builds a data dictionary
Step B: Dashboard Planning  → Plans KPIs, charts, filters, and layout
Step C: HTML Mock           → Generates an interactive HTML prototype (Chart.js)
Step D: Implementation Spec → Creates a detailed Tableau build blueprint
Step E: TWB Generation      → Produces a .twbx workbook file (experimental)
```

Each step outputs a versioned artifact. You review, request changes, or approve — then move on.

---

## Repository Structure

```
tableau-dashboard-creation-skill/
├── README.md                                    ← You are here
├── skill/
│   └── tableau-dashboard-creator/               ← The skill itself (copy this)
│       ├── SKILL.md                             # Skill definition & workflow
│       ├── assets/
│       │   └── fallback-design-tokens.md        # Default Tableau styling
│       ├── examples/
│       │   ├── simple-xml-example.twb           # Minimal TWB reference
│       │   ├── complicated-xml-example.twb      # Complex TWB reference
│       │   └── EtoroTableauTemplates.twb        # Real-world template example
│       ├── references/
│       │   ├── step-0-brand-setup.md            # Brand extraction instructions
│       │   ├── step-a-data-exploration.md       # Data analysis instructions
│       │   ├── step-b-dashboard-planning.md     # KPI/chart planning instructions
│       │   ├── step-c-mock-creation.md          # HTML mock generation instructions
│       │   ├── step-d-implementation-spec.md    # Tableau spec instructions
│       │   ├── step-e-twb-generation.md         # TWB XML generation instructions
│       │   ├── tableau-design-tokens.md         # Design token reference
│       │   └── twb-xml-reference.md             # TWB XML schema docs
│       └── scripts/
│           ├── query_databricks.py              # Databricks SQL executor
│           ├── query_postgresql.py              # PostgreSQL SQL executor
│           └── query_snowflake.py               # Snowflake SQL executor
└── demo/
    ├── input/                                   ← What YOU provide (example files)
    │   ├── SalesPerformance-PDR.md              # Completed dashboard request
    │   ├── EXAMPLE-PDR.md                       # Blank template for your own PDR
    │   ├── QUERIES.md                           # SQL query template
    │   ├── .env.example                         # Database credentials template
    │   ├── branding/
    │   │   └── palette.json                     # Color palette example
    │   └── sample-data/
    │       ├── sales_orders.csv                 # 40 sales transactions
    │       ├── customer_segments.csv            # 7 customer records
    │       └── monthly_targets.csv              # 12 monthly targets
    └── output/                                  ← What the SKILL generates
        ├── design-tokens.md                     # Step 0 output
        ├── DS-ARCHITECTURE.md                   # Step A output
        ├── DASHBOARD-PLAN.md                    # Step B output
        └── mock-version/v_1/
            ├── mock.html                        # Step C output (open in browser)
            ├── TABLEAU-IMPLEMENTATION.md         # Step D output
            ├── dashboard.twb                    # Step E output (raw XML)
            └── dashboard.twbx                   # Step E output (packaged workbook)
```

---

## Installation — Adding the Skill to Your Environment

### 1. Copy the skill directory

Copy the entire `skill/tableau-dashboard-creator/` folder into your Claude Code skills directory:

```bash
# The standard Claude Code skills location:
cp -r skill/tableau-dashboard-creator ~/.claude/skills/tableau-dashboard-creator
```

> The skill directory **must** contain `SKILL.md` at its root — this is what Claude Code uses to discover and invoke the skill.

### 2. Verify the skill is available

Open Claude Code and run:

```
/skill tableau-dashboard-creator
```

If the skill is installed correctly, Claude will begin the guided workflow.

---

## Quick Start — Creating Your First Dashboard

### 1. Set up your project directory

Create a new working directory for your dashboard project:

```bash
mkdir my-dashboard && cd my-dashboard
```

### 2. Provide your data (choose one)

**Option A — Local files (easiest):**
```bash
mkdir sample-data
# Place your CSV, JSON, or XLSX files in sample-data/
```

**Option B — SQL queries:**
Create a `QUERIES.md` file with queries grouped by database type:

```markdown
## Databricks
SELECT * FROM catalog.schema.my_table

## PostgreSQL
SELECT * FROM public.my_table
```

Then create a `.env` file with your database credentials (see `demo/input/.env.example` for the template).

### 3. Provide your branding (choose one)

**Option A — Tableau template (preferred):**
Place your organization's `template.twb` file in the project root.

**Option B — Logo + palette:**
```bash
mkdir branding
# Place logo.png/svg and palette.json/pdf in branding/
```

**Option C — No branding:**
The skill will use default Tableau styling (Open Sans, standard colors).

### 4. Write your dashboard request

Create a `<YOUR-NAME>-PDR.md` file describing what you want. Use `demo/input/EXAMPLE-PDR.md` as a template, or look at `demo/input/SalesPerformance-PDR.md` for a completed example.

A good PDR includes:
- **Overview** — Purpose, audience, update frequency
- **KPIs** — Metrics to track (with formulas if specific)
- **Visualizations** — Chart types and what they show
- **Filters** — What filtering options users need
- **Additional notes** — Colors, sorting, conditional formatting, etc.

### 5. Run the skill

```
/skill tableau-dashboard-creator
```

The skill will walk you through each step, presenting results for your approval.

---

## Understanding the Demo

The `demo/` directory contains a **complete worked example** — a Sales Performance dashboard — so you can see exactly what the skill produces at every step.

### Input files (`demo/input/`)

These are the files a user provides before running the skill:

| File | Purpose |
|------|---------|
| `SalesPerformance-PDR.md` | Dashboard request: 4 KPIs, 4 charts, 3 filters |
| `sample-data/*.csv` | Three CSV files with sales, customer, and target data |
| `branding/palette.json` | Corporate color palette |
| `EXAMPLE-PDR.md` | Blank template you can copy for your own project |
| `QUERIES.md` | SQL query template (not used in this demo — uses CSVs) |
| `.env.example` | DB credentials template (not used in this demo) |

### Output files (`demo/output/`)

These are the artifacts the skill generated from the inputs above:

| File | Step | What it contains |
|------|------|------------------|
| `design-tokens.md` | Step 0 | Typography, colors, spacing, layout templates |
| `DS-ARCHITECTURE.md` | Step A | Data dictionary, field types, join keys, quality notes |
| `DASHBOARD-PLAN.md` | Step B | KPI specs, chart types, filter strategy, layout choice |
| `mock-version/v_1/mock.html` | Step C | Interactive HTML prototype (open in browser!) |
| `mock-version/v_1/TABLEAU-IMPLEMENTATION.md` | Step D | Container tree, sheet specs, calculated fields, actions |
| `mock-version/v_1/dashboard.twbx` | Step E | Packaged Tableau workbook (open in Tableau Desktop) |

> Open `demo/output/mock-version/v_1/mock.html` in a browser to see the interactive mock prototype.

---

## Database Support

The skill includes query scripts for three databases. Required packages per database:

| Database | Packages | Key `.env` Variables |
|----------|----------|---------------------|
| Databricks | `databricks-sql-connector`, `pandas`, `python-dotenv` | `DATABRICKS_SERVER`, `DATABRICKS_HTTP_PATH`, `DATABRICKS_TOKEN` |
| PostgreSQL | `psycopg2-binary`, `pandas`, `python-dotenv` | `PG_HOST`, `PG_PORT`, `PG_DATABASE`, `PG_USER`, `PG_PASSWORD` |
| Snowflake | `snowflake-connector-python`, `pandas`, `python-dotenv` | `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, `SNOWFLAKE_PASSWORD`, `SNOWFLAKE_WAREHOUSE` |

All query scripts enforce a **LIMIT 500** safety cap on results.

---

## Project Output Structure

When you run the skill on your own project, it generates files in this structure:

```
your-project/
├── <YOUR-NAME>-PDR.md          # (you provide)
├── sample-data/ or QUERIES.md  # (you provide)
├── branding/ or template.twb   # (you provide)
├── design-tokens.md            # Generated — Step 0
├── DS-ARCHITECTURE.md          # Generated — Step A
├── DASHBOARD-PLAN.md           # Generated — Step B
└── mock-version/
    └── v_1/
        ├── mock.html                    # Generated — Step C
        ├── TABLEAU-IMPLEMENTATION.md    # Generated — Step D
        ├── dashboard.twb                # Generated — Step E
        └── dashboard.twbx               # Generated — Step E (primary deliverable)
```

Revisions increment the version: `v_2/`, `v_3/`, etc. Each version is a complete standalone copy.

---

## Key Constraints

- **No rounded corners** — Tableau doesn't support `border-radius` until 2026.1
- **No box shadows** — Not natively supported in Tableau
- **Container hierarchy** must follow Tableau's zone model (layout-basic → layout-flow → sheets)
- **Step E (TWB generation) is experimental** — Always review the generated workbook in Tableau Desktop before publishing