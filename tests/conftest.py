"""Shared pytest setup for the plugin's contract tests.

Each skill's helper script lives under ``skills/<skill>/scripts/``. Because those
directory names contain a hyphen they are not importable packages, so we add
each one to ``sys.path`` here, letting tests do a plain ``import route`` /
``import init``.
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SKILL_SCRIPT_DIRS = (
    _REPO_ROOT / "skills" / "tableau-route" / "scripts",
    _REPO_ROOT / "skills" / "tableau-init" / "scripts",
    _REPO_ROOT / "skills" / "tableau-intake" / "scripts",
    _REPO_ROOT / "skills" / "tableau-data" / "scripts",
    _REPO_ROOT / "skills" / "tableau-brand" / "scripts",
    _REPO_ROOT / "skills" / "tableau-plan" / "scripts",
    _REPO_ROOT / "skills" / "tableau-mock" / "scripts",
    _REPO_ROOT / "skills" / "tableau-spec" / "scripts",
    _REPO_ROOT / "skills" / "tableau-build" / "scripts",
)

for _script_dir in _SKILL_SCRIPT_DIRS:
    if str(_script_dir) not in sys.path:
        sys.path.insert(0, str(_script_dir))
