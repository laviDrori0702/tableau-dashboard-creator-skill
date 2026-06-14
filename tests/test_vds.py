"""Contract test for the published-ds route's VizQL Data Service client (CONTRACT.md §3.2).

``vds.py`` is the network half of ``tableau-data`` Route 2. Its job is mechanical and
must be pinned exactly: parse ``datasources.json``, load the ``.env`` connection, sign in
with a PAT, resolve each ``(ds_name, project_name)`` to a LUID, and make the two VDS
calls (``read-metadata`` + ``query-datasource``) with the precise request bodies the
contract specifies. Every failure mode must surface as an actionable :class:`vds.VdsError`
(never a silent fallback).

All HTTP is driven through an injected fake ``session`` (:class:`FakeSession`), so these
tests are deterministic and never touch the network — the same seam the real ``pull``
uses to talk to Tableau.
"""

from pathlib import Path

import pytest

import vds   # the module under test (on sys.path via conftest.py)


# --- A fake requests.Session (records calls, returns canned JSON) ------------

class FakeResponse:
    """A minimal stand-in for ``requests.Response`` (only what vds.py reads)."""

    def __init__(self, payload, status_code: int = 200, text: str = ""):
        self._payload = payload
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.text = text if text else (str(payload) if payload is not None else "")

    def json(self):
        if self._payload is _NOT_JSON:
            raise ValueError("No JSON object could be decoded")
        return self._payload


_NOT_JSON = object()  # sentinel: a response whose .json() raises (non-JSON body)


class FakeSession:
    """Routes POST/GET by URL to canned responses and records every request.

    Configure per-endpoint responses via the keyword payloads; any of them can be a
    :class:`FakeResponse` (to simulate an error status / non-JSON body) or a plain dict
    (wrapped in a 200 automatically).
    """

    def __init__(self, *, signin=None, datasources=None, metadata=None, query=None):
        self._routes = {
            "signin": signin,
            "datasources": datasources,
            "metadata": metadata,
            "query": query,
        }
        self.posts: list[dict] = []
        self.gets: list[dict] = []

    @staticmethod
    def _as_response(value) -> FakeResponse:
        return value if isinstance(value, FakeResponse) else FakeResponse(value)

    def _route_for(self, url: str) -> str:
        if "/auth/signin" in url:
            return "signin"
        if "read-metadata" in url:
            return "metadata"
        if "query-datasource" in url:
            return "query"
        if "/datasources" in url:
            return "datasources"
        raise AssertionError(f"unexpected URL in test: {url}")

    def post(self, url, json=None, headers=None, timeout=None):
        route = self._route_for(url)
        self.posts.append({"url": url, "json": json, "headers": headers, "route": route})
        return self._as_response(self._routes[route])

    def get(self, url, params=None, headers=None, timeout=None):
        self.gets.append({"url": url, "params": params, "headers": headers})
        return self._as_response(self._routes["datasources"])


def _conn() -> vds.TableauConnection:
    """A canned connection (no .env needed for the unit-level HTTP tests)."""
    return vds.TableauConnection(
        server="https://pod.online.tableau.com",
        site="mysite",
        api_version="3.24",
        pat_name="tok",
        pat_secret="secret",
    )


# --- slugify (CONTRACT.md §3.2) ----------------------------------------------

@pytest.mark.parametrize("name, expected", [
    ("Regional Sales", "regional_sales"),
    ("Superstore", "superstore"),
    ("Sales (2025) — EMEA", "sales_2025_emea"),
    ("  spaces  ", "spaces"),
    ("ALLCAPS", "allcaps"),
])
def test_slugify(name, expected):
    """ds_name -> CSV slug: lowercase, non-alphanumeric runs -> single underscore."""
    assert vds.slugify(name) == expected


def test_slugify_rejects_empty():
    """A name with no alphanumerics has no slug to build a filename from."""
    with pytest.raises(vds.VdsError):
        vds.slugify("!!!")


# --- vds_type_to_model_type --------------------------------------------------

@pytest.mark.parametrize("vds_type, expected", [
    ("STRING", "string"),
    ("integer", "integer"),       # case-insensitive
    ("REAL", "real"),
    ("DATE", "date"),
    ("DATETIME", "datetime"),
    ("BOOLEAN", "boolean"),
    ("SPATIAL", "string"),        # unknown -> string (still queryable, documented as text)
    ("", "string"),
])
def test_vds_type_to_model_type(vds_type, expected):
    """VDS dataType maps onto data.py's TYPES; unknown types fall back to string."""
    assert vds.vds_type_to_model_type(vds_type) == expected


# --- parse_datasources_json --------------------------------------------------

def test_parse_datasources_json_ignores_comment_keys(tmp_path):
    """`_`-prefixed keys (e.g. _comment) are skipped; real entries keep file order."""
    path = tmp_path / "datasources.json"
    path.write_text(
        '{"_comment": "ignore me", '
        '"ds_1": {"ds_name": "Superstore", "project_name": "Samples"}, '
        '"ds_2": {"ds_name": "Regional Sales", "project_name": "Sales"}}',
        encoding="utf-8",
    )

    refs = vds.parse_datasources_json(path)

    assert [r.ds_name for r in refs] == ["Superstore", "Regional Sales"]
    assert refs[0].project_name == "Samples" and refs[0].key == "ds_1"


def test_parse_datasources_json_requires_name_and_project(tmp_path):
    """An entry missing ds_name/project_name is an actionable error."""
    path = tmp_path / "datasources.json"
    path.write_text('{"ds_1": {"ds_name": "Superstore"}}', encoding="utf-8")

    with pytest.raises(vds.VdsError, match="ds_name.*project_name|project_name"):
        vds.parse_datasources_json(path)


def test_parse_datasources_json_refuses_when_only_comments(tmp_path):
    """A file with only comment keys lists no sources -> refuse."""
    path = tmp_path / "datasources.json"
    path.write_text('{"_comment": "nothing real here"}', encoding="utf-8")

    with pytest.raises(vds.VdsError, match="no data sources"):
        vds.parse_datasources_json(path)


# --- .env discovery + load_connection ----------------------------------------

def _write_env(directory: Path, **overrides: str) -> Path:
    """Write a .env with a full valid connection, applying overrides (None drops a key)."""
    fields = {
        "TABLEAU_SERVER": "https://pod.online.tableau.com/",   # trailing slash trimmed
        "TABLEAU_SITE": "mysite",
        "TABLEAU_API_VERSION": "3.24",
        "TABLEAU_PAT_NAME": "tok",
        "TABLEAU_PAT_SECRET": "secret",
    }
    fields.update(overrides)
    lines = [f"{key}={value}" for key, value in fields.items() if value is not None]
    path = directory / ".env"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_find_env_walks_up(tmp_path):
    """find_env returns the nearest .env walking up from the project dir."""
    _write_env(tmp_path)
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)

    assert vds.find_env(nested) == (tmp_path / ".env")


def test_load_connection_reads_and_trims(tmp_path):
    """A full .env loads into a TableauConnection; the server trailing slash is trimmed."""
    _write_env(tmp_path)

    conn = vds.load_connection(tmp_path)

    assert conn.server == "https://pod.online.tableau.com"   # no trailing slash
    assert conn.site == "mysite" and conn.api_version == "3.24"
    assert conn.pat_name == "tok" and conn.pat_secret == "secret"


def test_load_connection_defaults_api_version(tmp_path):
    """An absent TABLEAU_API_VERSION falls back to the default; site may be empty."""
    _write_env(tmp_path, TABLEAU_API_VERSION=None, TABLEAU_SITE="")

    conn = vds.load_connection(tmp_path)

    assert conn.api_version == vds.DEFAULT_API_VERSION
    assert conn.site == ""   # default site


def test_load_connection_errors_on_missing_pat(tmp_path):
    """A missing required var is an actionable error naming the key."""
    _write_env(tmp_path, TABLEAU_PAT_SECRET=None)

    with pytest.raises(vds.VdsError, match="TABLEAU_PAT_SECRET"):
        vds.load_connection(tmp_path)


def test_load_connection_errors_when_no_env(tmp_path):
    """No .env anywhere -> a clear 'copy scaffold/.env.example' error."""
    with pytest.raises(vds.VdsError, match="No .env"):
        vds.load_connection(tmp_path)


# --- sign_in -----------------------------------------------------------------

def test_sign_in_builds_pat_body_and_parses_token():
    """sign_in posts the PAT credentials body and returns (token, site_id)."""
    session = FakeSession(
        signin={"credentials": {"token": "auth-tok", "site": {"id": "site-99"}}}
    )

    token, site_id = vds.sign_in(_conn(), session)

    assert (token, site_id) == ("auth-tok", "site-99")
    sent = session.posts[0]["json"]["credentials"]
    assert sent["personalAccessTokenName"] == "tok"
    assert sent["personalAccessTokenSecret"] == "secret"
    assert sent["site"] == {"contentUrl": "mysite"}
    assert session.posts[0]["url"].endswith("/api/3.24/auth/signin")


def test_sign_in_raises_on_bad_status():
    """A 401 (bad PAT) surfaces as VdsError carrying the server message."""
    session = FakeSession(signin=FakeResponse({}, status_code=401, text="invalid token"))

    with pytest.raises(vds.VdsError, match="401"):
        vds.sign_in(_conn(), session)


# --- resolve_luid ------------------------------------------------------------

def _datasources_payload(*entries) -> dict:
    """Build a REST Query-Data-Sources body from (id, name, project) triples."""
    return {"datasources": {"datasource": [
        {"id": luid, "name": name, "project": {"name": project}}
        for luid, name, project in entries
    ]}}


def test_resolve_luid_matches_name_and_project():
    """The LUID is the entry matching BOTH name and project; name filter is sent."""
    session = FakeSession(datasources=_datasources_payload(
        ("luid-other", "Superstore", "Wrong Project"),
        ("luid-1", "Superstore", "Samples"),
    ))

    luid = vds.resolve_luid(_conn(), "tok", "site-1", "Superstore", "Samples", session)

    assert luid == "luid-1"
    assert session.gets[0]["params"]["filter"] == "name:eq:Superstore"
    assert session.gets[0]["headers"]["X-Tableau-Auth"] == "tok"


def test_resolve_luid_errors_and_names_embedded_possibility():
    """No name+project match -> error that names the embedded/export-to-CSV fallback."""
    session = FakeSession(datasources=_datasources_payload(
        ("luid-1", "Superstore", "Other"),
    ))

    with pytest.raises(vds.VdsError, match="EMBEDDED|export the data to CSV"):
        vds.resolve_luid(_conn(), "tok", "site-1", "Superstore", "Samples", session)


# --- read_metadata -----------------------------------------------------------

def test_read_metadata_parses_fields_and_maps_types():
    """read-metadata returns captions/types/descriptions; the body targets the LUID."""
    session = FakeSession(metadata={"data": [
        {"fieldCaption": "Order ID", "dataType": "STRING", "description": "the id"},
        {"fieldCaption": "Revenue", "dataType": "REAL"},
        {"fieldCaption": "", "dataType": "STRING"},   # no caption -> skipped
    ]})

    fields = vds.read_metadata(_conn(), "tok", "luid-1", session)

    assert [f.caption for f in fields] == ["Order ID", "Revenue"]
    assert fields[0].model_type == "string" and fields[0].description == "the id"
    assert fields[1].model_type == "real" and fields[1].description == ""
    assert session.posts[0]["json"] == {"datasource": {"datasourceLuid": "luid-1"}}


def test_read_metadata_errors_when_no_queryable_fields():
    """Empty metadata -> error pointing at the 'API Access' capability."""
    session = FakeSession(metadata={"data": []})

    with pytest.raises(vds.VdsError, match="API Access"):
        vds.read_metadata(_conn(), "tok", "luid-1", session)


# --- query_rows --------------------------------------------------------------

def test_query_rows_builds_contract_body_and_returns_rows():
    """The query body matches CONTRACT.md §3.2 exactly (fields + rowLimit)."""
    session = FakeSession(query={"data": [
        {"Order ID": "ORD-1", "Revenue": 10.5},
        {"Order ID": "ORD-2", "Revenue": 20.0},
    ]})

    rows = vds.query_rows(_conn(), "tok", "luid-1", ["Order ID", "Revenue"], 100, session)

    assert len(rows) == 2 and rows[0]["Order ID"] == "ORD-1"
    body = session.posts[0]["json"]
    assert body["datasource"] == {"datasourceLuid": "luid-1"}
    assert body["query"] == {"fields": [{"fieldCaption": "Order ID"},
                                        {"fieldCaption": "Revenue"}]}
    assert body["options"] == {"rowLimit": 100}


def test_query_rows_errors_on_zero_rows():
    """Zero rows -> error (no synthesized-data fallback, CONTRACT.md §3.2)."""
    session = FakeSession(query={"data": []})

    with pytest.raises(vds.VdsError, match="zero rows"):
        vds.query_rows(_conn(), "tok", "luid-1", ["Order ID"], 100, session)
