"""Execute Snowflake SQL queries with LIMIT 500 enforcement.

Usage:
    python scripts/query_snowflake.py "SELECT * FROM db.schema.table"
    python scripts/query_snowflake.py --file queries.sql --output results/

Requires .env with: SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PASSWORD, SNOWFLAKE_WAREHOUSE
Database and schema are extracted from the SQL query itself (FROM db.schema.table).
Packages: snowflake-connector-python, python-dotenv, pandas
"""

import argparse
import os
import re

import pandas as pd
import snowflake.connector
from dotenv import load_dotenv

MAX_ROWS = 500


def enforce_limit(query: str, limit: int = MAX_ROWS) -> str:
    """Append LIMIT clause if not already present.

    Args:
        query: SQL query string
        limit: Maximum number of rows to return

    Returns:
        Query string with LIMIT clause enforced
    """
    stripped = query.rstrip().rstrip(";")
    if re.search(r"\bLIMIT\s+\d+", stripped, re.IGNORECASE):
        return stripped
    return f"{stripped}\nLIMIT {limit}"


def get_connection() -> snowflake.connector.SnowflakeConnection:
    """Create a Snowflake connection from .env credentials.

    Database and schema are NOT required here — they are extracted from the
    fully-qualified table names in the SQL queries (e.g., FROM db.schema.table).

    Returns:
        Active Snowflake connection

    Raises:
        EnvironmentError: If required env vars are missing
    """
    load_dotenv()
    account = os.getenv("SNOWFLAKE_ACCOUNT")
    user = os.getenv("SNOWFLAKE_USER")
    password = os.getenv("SNOWFLAKE_PASSWORD")
    warehouse = os.getenv("SNOWFLAKE_WAREHOUSE")

    missing = [v for v, val in [
        ("SNOWFLAKE_ACCOUNT", account),
        ("SNOWFLAKE_USER", user),
        ("SNOWFLAKE_PASSWORD", password),
        ("SNOWFLAKE_WAREHOUSE", warehouse),
    ] if not val]
    if missing:
        raise EnvironmentError(f"Missing env vars: {', '.join(missing)}")

    return snowflake.connector.connect(
        account=account,
        user=user,
        password=password,
        warehouse=warehouse,
    )


def run_query(query: str) -> pd.DataFrame:
    """Execute a query and return results as a DataFrame.

    Args:
        query: SQL query string (use fully-qualified table names: db.schema.table)

    Returns:
        DataFrame with query results
    """
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


def main() -> None:
    """CLI entrypoint for Snowflake query execution."""
    parser = argparse.ArgumentParser(description="Run Snowflake query with LIMIT 500")
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