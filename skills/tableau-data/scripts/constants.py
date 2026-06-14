"""Canonical constants for the tableau-data skill (mirror of CONTRACT.md §1 / §2 / §3).

These are the contract-level names and policy values shared by the data step's three
modules: :mod:`datamodel` (profiling + DATA-MODEL.md), :mod:`state` (STATE.md
transitions + entry gate), and :mod:`data` (the command layer + CLI). Keeping them in
one dependency-free leaf module is what lets those three import the same constants
without a circular import.

Keep ``STEP_ORDER`` below in lock-step with the ordered step list in CONTRACT.md §1.
"""

from __future__ import annotations

STATE_FILENAME = "STATE.md"
DATA_MODEL_FILENAME = "DATA-MODEL.md"

#: This step, and the upstream step whose approval gates it (CONTRACT.md §4.1).
DATA_STEP = "data"
INIT_STEP = "init"

#: The 8 step names in canonical order (mirror of CONTRACT.md §1). Used to decide
#: which steps are "downstream of data" for staleness propagation (§4.2).
STEP_ORDER: tuple[str, ...] = (
    "init", "intake", "data", "brand", "plan", "mock", "spec", "build",
)

#: Route 1 (csv): production CSVs (preferred) and the scaffold/ demo fallback (§3.1).
DATA_DIR = "data"
SCAFFOLD_DATA_DIR = "scaffold/sample-data"

#: Route 2 (published-ds): the inputs the VDS pull reads (CONTRACT.md §3.2).
DATASOURCES_FILENAME = "datasources.json"
ENV_FILENAME = ".env"

#: STATE.md ``data_mode`` metadata values (CONTRACT.md §2). A successful ``pull`` flips
#: the recorded mode to ``published-ds``; ``profile`` leaves the default ``csv``.
DATA_MODE_CSV = "csv"
DATA_MODE_PUBLISHED_DS = "published-ds"

#: Row-limit policy for the VDS sample (CONTRACT.md §3.2): default 100, silent up to
#: 1000, and a value above 1000 needs explicit analyst confirmation before the pull.
DEFAULT_ROW_LIMIT = 100
MAX_SILENT_ROW_LIMIT = 1000

#: The Tableau-friendly column types ``profile`` infers and ``DATA-MODEL.md`` records.
#: Ordered narrowest-first; that order is the inference precedence in ``infer_type``.
TYPE_BOOLEAN = "boolean"
TYPE_INTEGER = "integer"
TYPE_REAL = "real"
TYPE_DATE = "date"
TYPE_DATETIME = "datetime"
TYPE_STRING = "string"
TYPES: tuple[str, ...] = (
    TYPE_BOOLEAN, TYPE_INTEGER, TYPE_REAL, TYPE_DATE, TYPE_DATETIME, TYPE_STRING,
)

#: Acquisition tiers recorded in DATA-MODEL.md (CONTRACT.md §3.2). The csv route
#: produces one of the first two; the published-ds route (``pull``) records the third.
TIER_CSV_PROVIDED = "csv (provided in data/)"
TIER_CSV_DEMO = "csv (demo - scaffold/sample-data/)"
TIER_PUBLISHED_DS = "published-ds (VDS query)"

#: How many data rows ``profile`` reads to infer types and gather sample values.
PROFILE_SAMPLE_ROWS = 200
#: How many distinct sample values to show per field in DATA-MODEL.md.
SAMPLE_VALUES_SHOWN = 3
