"""Guards on what the repository is allowed to publish.

This is a public plugin, and the reference `.twb` snippets under `skills/` and `skill/` are
real Desktop output - which means they arrive carrying whatever the authoring machine put in
them. A Tableau `<connection>` records the absolute path of its data file, so a snippet saved
from a Windows home directory embeds that user's account name. Thirty-one tracked files
carried one before it was noticed (issue #50's review), so the fix needs a test, not a habit.

The check is deliberately about *shape*, not about one name: any absolute home-directory path
fails, whoever authored it.
"""

import re
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

#: The placeholder a masked path uses. XML-safe on purpose - '<user>' is not, because a '<'
#: inside an attribute value makes the workbook unparseable (which is how the first attempt
#: at this masking was caught).
USERNAME_PLACEHOLDER = "%USERNAME%"

#: A Windows or POSIX home directory with a real account name in it. The placeholder and the
#: literal 'Users/' with nothing after it are the only accepted forms.
_HOME_PATH = re.compile(
    r"(?:[A-Za-z]:)?[/\\]{1,2}(?:Users|home)[/\\]{1,2}(?!%USERNAME%|<user>)([A-Za-z0-9._-]+)[/\\]",
)

#: Text formats a path can hide in. Binary '.twbx' archives are not scanned - they are zipped,
#: so a grep would miss the path anyway; keep reference snippets unpackaged so this can see them.
_TEXT_SUFFIXES = (".twb", ".md", ".json", ".py", ".xml", ".txt", ".tds", ".yml", ".yaml")


#: This file is exempt from its own scan: the fixtures in
#: :func:`test_the_check_would_catch_an_unmasked_path` are unmasked paths on purpose. The
#: exemption is one file wide and named literally, so it cannot be used to hide a real leak.
_SELF = Path(__file__).name


def _tracked_text_files():
    """list[Path]: every git-tracked file this check can read as text, minus this one."""
    listing = subprocess.run(
        ["git", "ls-files"], cwd=_REPO_ROOT,
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    return [
        _REPO_ROOT / name
        for name in listing
        if name.endswith(_TEXT_SUFFIXES) and not name.endswith(f"tests/{_SELF}")
    ]


def test_no_tracked_file_embeds_a_home_directory_path():
    """A real account name in a tracked file is published the moment the repo is.

    Tableau writes the data file's absolute path into `<connection>`, so saving a reference
    workbook from a home directory leaks the author's username. Mask it with
    :data:`USERNAME_PLACEHOLDER` - the path stays realistic and the account name does not ship.
    """
    offenders = []
    for path in _tracked_text_files():
        try:
            text = path.read_text(encoding="utf8")
        except (UnicodeDecodeError, OSError):
            continue  # not text after all, or unreadable - nothing to inspect
        for match in _HOME_PATH.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            offenders.append(
                f"{path.relative_to(_REPO_ROOT).as_posix()}:{line}: {match.group(0)!r}"
            )

    assert not offenders, (
        "tracked files embed a home-directory path; replace the account name with "
        f"{USERNAME_PLACEHOLDER}:\n  " + "\n  ".join(offenders)
    )


def test_the_check_would_catch_an_unmasked_path():
    """The guard above passes trivially once the repo is clean, so prove it still bites.

    Without this, deleting the pattern's body would leave a green test that checks nothing.
    """
    assert _HOME_PATH.search("filename='C:/Users/someone/Documents/data.hyper'")
    assert _HOME_PATH.search("/home/someone/data.csv")
    # The masked form and a bare 'Users/' are the accepted shapes.
    assert not _HOME_PATH.search("filename='C:/Users/%USERNAME%/Documents/data.hyper'")
