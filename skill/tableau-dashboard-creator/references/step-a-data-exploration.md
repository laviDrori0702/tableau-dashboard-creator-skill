# Step A: Data Exploration

**Identity**: You are a senior data engineer. Your goal is to understand the data landscape so the dashboard can be planned effectively.

## Data Source Detection

Before executing any queries, check for local data files:

### Priority 1: Local Files (`sample-data/` directory)

If the user's project root contains a `sample-data/` directory with data files:

1. **Scan the directory** for supported file types: `.csv`, `.json`, `.xlsx`
2. **Load each file** using pandas:
   - CSV: `pd.read_csv(path)`
   - JSON: `pd.read_json(path)`
   - XLSX: `pd.read_excel(path)` (read all sheets if multiple exist)
3. **Analyze** each file as if it were a query result (see Analysis section below)
4. **Skip database queries entirely** — do not require `.env` or `QUERIES.md`

When using local files, note in DS-ARCHITECTURE.md that the datasource is a local file (with filename) rather than a database query.

### Priority 2: Database Queries (`QUERIES.md`)

If no `sample-data/` directory exists (or it is empty), read `QUERIES.md` and execute queries:

1. **Parse QUERIES.md** — queries are grouped under headings that indicate the database type:
   - `## Databricks` → use `scripts/query_databricks.py`
   - `## PostgreSQL` → use `scripts/query_postgresql.py`
   - `## Snowflake` → use `scripts/query_snowflake.py`

2. **Execute each query** using the matching script:

```bash
# Databricks
python scripts/query_databricks.py "SELECT * FROM catalog.schema.table"

# PostgreSQL
python scripts/query_postgresql.py "SELECT * FROM public.table"

# Snowflake
python scripts/query_snowflake.py "SELECT * FROM db.schema.table"
```

All scripts enforce LIMIT 500 automatically. If the user's query already has a LIMIT, it is preserved.

## Analysis

For each datasource (whether from local files or query results), analyze:

- Column names, data types, and cardinality
- Sample values and value distributions
- Null rates and data quality observations
- Identify date/time columns, dimensions, and measures
- Spot relationships between datasources (shared keys)

## Create DS-ARCHITECTURE.md

Use the template below:

```markdown
# Data Source Architecture

## Overview
[Brief summary of the datasources and how they relate to the dashboard request]

---

## Datasource 1: [Table/View Name or File Name]

**Source**: `[SQL query used]` or `[sample-data/filename.csv]`
**Rows sampled**: [N]
**Description**: [What this datasource represents]

### Fields

| Field | Type | Description | Sample Values | Notes |
|-------|------|-------------|---------------|-------|
| field_name | STRING | [What this field represents] | val1, val2, val3 | [Nullable/unique/FK] |
| ... | ... | ... | ... | ... |

### Observations
- [Key patterns noticed in the data]
- [Data quality notes]
- [Potential join keys or relationships]

---

## Datasource 2: [Table/View Name or File Name]
[Same structure as above]

---

## Relationships
[Describe how datasources connect - shared keys, join conditions]

## Data Quality Notes
[Any concerns: nulls, duplicates, unexpected values, type mismatches]
```

## Guidelines

- Write field descriptions that a data analyst (not an engineer) can understand
- Flag fields that look like they could serve as dimensions vs. measures
- If a query fails, document the error and suggest fixes
- If data looks sparse or suspicious, note it in observations
- When using local files, note the file format and any parsing considerations (encoding, delimiters)
- Present DS-ARCHITECTURE.md to the user and **wait for approval** before proceeding to Step B