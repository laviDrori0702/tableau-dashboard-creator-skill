"""Execute Databricks SQL queries with LIMIT 500 enforcement.

Usage:
    python scripts/query_databricks.py "SELECT * FROM catalog.schema.table"
    python scripts/query_databricks.py --file queries.sql --output results/

Requires .env with: DATABRICKS_SERVER, DATABRICKS_HTTP_PATH, DATABRICKS_TOKEN
Packages: databricks-sql-connector, python-dotenv, pandas
"""

import argparse
import os
import re
import sys

import pandas as pd
from databricks import sql
from dotenv import load_dotenv

MAX_ROWS = 500


def enforce_limit(query: str, limit: int = MAX_ROWS) -> str:
    """Append LIMIT clause if not already present."""
    stripped = query.rstrip().rstrip(";")
    if re.search(r"\bLIMIT\s+\d+", stripped, re.IGNORECASE):
        return stripped
    return f"{stripped}\nLIMIT {limit}"


def get_connection():
    load_dotenv()
    server = os.getenv("DATABRICKS_SERVER")
    http_path = os.getenv("DATABRICKS_HTTP_PATH")
    token = os.getenv("DATABRICKS_TOKEN")

    missing = [v for v, val in [
        ("DATABRICKS_SERVER", server),
        ("DATABRICKS_HTTP_PATH", http_path),
        ("DATABRICKS_TOKEN", token),
    ] if not val]
    if missing:
        raise EnvironmentError(f"Missing env vars: {', '.join(missing)}")

    return sql.connect(
        server_hostname=server,
        http_path=http_path,
        access_token=token,
    )


def run_query(query: str) -> pd.DataFrame:
    """Execute a query and return results as a DataFrame."""
    safe_query = enforce_limit(query)
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(safe_query)
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        return pd.DataFrame(rows, columns=columns)
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Run Databricks SQL with LIMIT 500")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("query", nargs="?", help="SQL query string")
    group.add_argument("--file", "-f", help="Path to .sql file")
    parser.add_argument("--output", "-o", default=".", help="Output directory for CSV")
    args = parser.parse_args()

    query = args.query
    if args.file:
        with open(args.file, "r") as f:
            query = f.read()

    os.makedirs(args.output, exist_ok=True)

    print(f"Executing query (LIMIT {MAX_ROWS} enforced)...")
    df = run_query(query)

    csv_path = os.path.join(args.output, "query_results.csv")
    df.to_csv(csv_path, index=False)
    print(f"\nRows returned: {len(df)}")
    print(f"Columns: {list(df.columns)}")
    print(f"CSV saved to: {csv_path}")
    print(f"\nSample (first 10 rows):\n{df.head(10).to_string()}")


if __name__ == "__main__":
    main()
