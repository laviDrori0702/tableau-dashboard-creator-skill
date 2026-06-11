"""Shared pytest setup for the plugin's contract tests.

The router lives at ``skills/tableau-route/route.py``. Because that directory
name contains a hyphen it is not an importable package, so we add it to
``sys.path`` here, letting tests do a plain ``import route``.
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ROUTE_DIR = _REPO_ROOT / "skills" / "tableau-route"

if str(_ROUTE_DIR) not in sys.path:
    sys.path.insert(0, str(_ROUTE_DIR))
