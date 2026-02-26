"""Execute PostgreSQL queries with LIMIT 500 enforcement.

Usage:
    python scripts/query_postgresql.py "SELECT * FROM public.your_table"
    python scripts/query_postgresql.py --file queries.sql --output results/

Requires .env with: PG_HOST, PG_PORT, PG_DATABASE, PG_USER, PG_PASSWORD
Packages: psycopg2-binary, python-dotenv, pandas
"""

import argparse
import os
import re

import pandas as pd
import psycopg2
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


def get_connection() -> psycopg2.extensions.connection:
    """Create a PostgreSQL connection from .env credentials.

    Returns:
        Active psycopg2 connection

    Raises:
        EnvironmentError: If required env vars are missing
    """
    load_dotenv()
    host = os.getenv("PG_HOST")
    port = os.getenv("PG_PORT", "5432")
    database = os.getenv("PG_DATABASE")
    user = os.getenv("PG_USER")
    password = os.getenv("PG_PASSWORD")

    missing = [v for v, val in [
        ("PG_HOST", host),
        ("PG_DATABASE", database),
        ("PG_USER", user),
        ("PG_PASSWORD", password),
    ] if not val]
    if missing:
        raise EnvironmentError(f"Missing env vars: {', '.join(missing)}")

    return psycopg2.connect(
        host=host,
        port=int(port),
        dbname=database,
        user=user,
        password=password,
    )


def run_query(query: str) -> pd.DataFrame:
    """Execute a query and return results as a DataFrame.

    Args:
        query: SQL query string

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
    """CLI entrypoint for PostgreSQL query execution."""
    parser = argparse.ArgumentParser(description="Run PostgreSQL query with LIMIT 500")
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