# QUERIES.md

Provide your SQL queries below. Group them under a heading that matches your database type.
The agent will use the heading to select the correct query script.

Supported database types: `Databricks`, `PostgreSQL`, `Snowflake`

---

## Databricks

```sql
-- Example: replace with your actual query
SELECT * FROM catalog.schema.your_table
```

---

## PostgreSQL

```sql
-- Example: replace with your actual query
SELECT * FROM public.your_table
```

---

## Snowflake

```sql
-- Example: replace with your actual query
SELECT * FROM your_database.your_schema.your_table
```

---

**Note**: You only need to include sections for the database(s) you actually use. Delete the rest.
If you placed data files in `sample-data/`, this file is optional — the agent will scan local files first.