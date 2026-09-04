"""VizQL Data Service (VDS) client for the ``tableau-data`` published-ds route.

This module is the network half of ``tableau-data`` Route 2 (CONTRACT.md §3.2): it
samples *published* Tableau data sources through Tableau's official VizQL Data Service
and hands the result back to ``data.py``, which turns it into ``data/<slug>.csv`` files
and ``DATA-MODEL.md``. It deliberately owns **only** the network concerns
(authentication, LUID resolution, the two VDS calls); the orchestration, file writing,
and STATE.md transition live in ``data.py`` (CONTRACT.md §7 — each skill owns its own
VDS client).

The contract pins the shape of the work, and this module implements exactly that and
nothing more:

1. **PAT sign-in** — a Tableau REST Personal Access Token sign-in
   (``POST /api/<ver>/auth/signin``) returns a credentials token reused for VDS.
2. **LUID resolution** — ``datasources.json`` names sources by ``(ds_name,
   project_name)``; VDS addresses them by ``datasourceLuid``, so we look the LUID up via
   the REST ``Query Data Sources`` endpoint.
3. **``read-metadata``** — ``POST /api/v1/vizql-data-service/read-metadata`` returns the
   queryable fields (caption, type, description). These are *authoritative*: the pulled
   CSV schema and ``DATA-MODEL.md`` take their types/descriptions from here.
4. **``query-datasource``** — ``POST /api/v1/vizql-data-service/query-datasource`` pulls
   a capped sample of all queryable fields.

There is **no** extract download, GraphQL, embedded-source reading, or synthesized data
(CONTRACT.md §3.2). VDS requires **Tableau Cloud, or Tableau Server 2025.1+**, and the
data source must have the **API Access** capability enabled.

Every HTTP function takes an injectable ``session`` (a ``requests.Session``) so the
contract tests can drive the client with a fake transport and never touch the network.
Failures raise :class:`VdsError` with an analyst-actionable message; ``data.py`` relays
it and writes no artifact.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    import requests
except ImportError:
    # Guarded like lxml in tableau-build's validate_twb_xsd.py: the published-ds route is
    # the only third-party-dependent path in the plugin, so name the install explicitly.
    print('ERROR: requests is required by the published-ds route. Install with: '
          'pip install -r "${CLAUDE_PLUGIN_ROOT}/requirements.txt"', file=sys.stderr)
    sys.exit(2)

logger = logging.getLogger(__name__)

# --- Canonical constants (mirror of CONTRACT.md §3.2 and scaffold/.env.example) ---

#: Default Tableau REST API version when ``.env`` does not pin one. 3.24 ships with the
#: 2025.1 server family that first carries VDS on Server; Cloud tracks the latest.
DEFAULT_API_VERSION = "3.24"

#: ``.env`` keys read for the connection (see scaffold/.env.example).
ENV_SERVER = "TABLEAU_SERVER"
ENV_SITE = "TABLEAU_SITE"
ENV_API_VERSION = "TABLEAU_API_VERSION"
ENV_PAT_NAME = "TABLEAU_PAT_NAME"
ENV_PAT_SECRET = "TABLEAU_PAT_SECRET"

#: The VDS field ``dataType`` values mapped onto data.py's TYPES vocabulary. Anything
#: not listed (e.g. ``SPATIAL``) falls back to ``string`` — it is still queryable, we
#: just document it as text.
_VDS_TYPE_MAP = {
    "STRING": "string",
    "INTEGER": "integer",
    "REAL": "real",
    "DATE": "date",
    "DATETIME": "datetime",
    "BOOLEAN": "boolean",
}
_FALLBACK_MODEL_TYPE = "string"

#: VDS endpoints are versioned under a fixed ``/api/v1/`` path, independent of the REST
#: API version used for sign-in / LUID lookup.
_VDS_BASE_PATH = "/api/v1/vizql-data-service"

#: A short network timeout (connect, read) so a hung site fails fast with a clear error.
_HTTP_TIMEOUT = (10, 60)


class VdsError(Exception):
    """A published-ds pull failure with an analyst-actionable message.

    Raised for every failure mode CONTRACT.md §3.2 enumerates — sign-in/connection
    failure, the source not resolving as a *published* source, the **API Access**
    capability being off, or a query returning no rows. ``data.py`` catches it, relays
    ``str(error)``, and writes no artifact (STATE.md untouched).
    """


# --- Connection config (.env) ------------------------------------------------

@dataclass(frozen=True)
class TableauConnection:
    """A resolved Tableau connection read from the nearest ``.env``.

    Attributes:
        server: Base server / pod URL, e.g. ``https://10ax.online.tableau.com`` (no
            trailing slash).
        site: The site *content URL* (the token in the site's URL); empty string for
            the default site.
        api_version: The REST API version for sign-in and LUID lookup (e.g. ``3.24``).
        pat_name: Personal Access Token name.
        pat_secret: Personal Access Token secret.
    """

    server: str
    site: str
    api_version: str
    pat_name: str
    pat_secret: str

    def rest_url(self, path: str) -> str:
        """str: Build a versioned REST URL, e.g. ``…/api/3.24/auth/signin``."""
        return f"{self.server}/api/{self.api_version}{path}"

    def vds_url(self, endpoint: str) -> str:
        """str: Build a VDS URL, e.g. ``…/api/v1/vizql-data-service/read-metadata``."""
        return f"{self.server}{_VDS_BASE_PATH}/{endpoint}"


def find_env(project_root: Path | str) -> Optional[Path]:
    """Find the nearest ``.env`` by walking up from the project directory.

    The nearest file wins (CONTRACT.md §3.2): the project directory is checked first,
    then each ancestor up to the filesystem root.

    Args:
        project_root: The analyst's project directory.

    Returns:
        The path to the closest ``.env``, or ``None`` if none exists on the way up.
    """
    start = Path(project_root).resolve()
    for directory in (start, *start.parents):
        candidate = directory / ".env"
        if candidate.is_file():
            return candidate
    return None


def load_connection(project_root: Path | str) -> TableauConnection:
    """Load and validate the Tableau connection from the nearest ``.env``.

    Uses ``python-dotenv`` (already a shared dependency) to read the ``.env`` without
    mutating ``os.environ``. The site content URL is optional (empty = default site)
    and the API version defaults to :data:`DEFAULT_API_VERSION`; everything else is
    required.

    Args:
        project_root: The analyst's project directory (the search starts here).

    Returns:
        A validated :class:`TableauConnection`.

    Raises:
        VdsError: If no ``.env`` is found, or a required variable is missing/blank.
    """
    try:
        # Local import: only the published-ds route needs it.
        from dotenv import dotenv_values
    except ImportError as exc:
        raise VdsError(
            'python-dotenv is required to read .env for the published-ds route. '
            'Install with: pip install -r "${CLAUDE_PLUGIN_ROOT}/requirements.txt"'
        ) from exc

    env_path = find_env(project_root)
    if env_path is None:
        raise VdsError(
            "No .env found walking up from the project directory. The published-ds "
            "route needs Tableau credentials: copy scaffold/.env.example to .env at the "
            "project root and fill in your TABLEAU_* connection."
        )

    values = dotenv_values(env_path)
    logger.info(f"Loaded Tableau connection from {env_path}.")

    def _required(key: str) -> str:
        value = (values.get(key) or "").strip()
        if not value:
            raise VdsError(
                f"Required variable '{key}' is missing or blank in {env_path}. "
                f"See scaffold/.env.example for the full published-ds connection."
            )
        return value

    server = _required(ENV_SERVER).rstrip("/")
    return TableauConnection(
        server=server,
        site=(values.get(ENV_SITE) or "").strip(),
        api_version=(values.get(ENV_API_VERSION) or "").strip() or DEFAULT_API_VERSION,
        pat_name=_required(ENV_PAT_NAME),
        pat_secret=_required(ENV_PAT_SECRET),
    )


# --- datasources.json --------------------------------------------------------

@dataclass(frozen=True)
class DatasourceRef:
    """One published data source the analyst asked to sample.

    Attributes:
        key: The entry id in ``datasources.json`` (e.g. ``ds_1``); used only for
            error messages.
        ds_name: The published data source's name on the site.
        project_name: The Tableau project the source lives in; with ``ds_name`` it
            identifies the source uniquely.
    """

    key: str
    ds_name: str
    project_name: str


def parse_datasources_json(path: Path | str) -> list[DatasourceRef]:
    """Parse ``datasources.json`` into an ordered list of data-source references.

    Keys starting with ``_`` are ignored (they are comments, e.g. ``_comment`` in the
    scaffold example). Each remaining entry must carry both ``ds_name`` and
    ``project_name`` (CONTRACT.md §3.2).

    Args:
        path: Path to ``datasources.json``.

    Returns:
        The referenced sources in file order.

    Raises:
        VdsError: If the file is missing, not valid JSON, has no usable entries, or an
            entry omits ``ds_name`` / ``project_name``.
    """
    json_path = Path(path)
    if not json_path.is_file():
        raise VdsError(f"datasources.json not found at {json_path}.")

    try:
        raw = json.loads(json_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        raise VdsError(f"datasources.json is not valid JSON: {error}.")
    if not isinstance(raw, dict):
        raise VdsError("datasources.json must be a JSON object keyed by data-source id.")

    refs: list[DatasourceRef] = []
    for key, entry in raw.items():
        if key.startswith("_"):
            continue
        if not isinstance(entry, dict):
            raise VdsError(f"datasources.json entry '{key}' must be an object.")
        ds_name = (entry.get("ds_name") or "").strip()
        project_name = (entry.get("project_name") or "").strip()
        if not ds_name or not project_name:
            raise VdsError(
                f"datasources.json entry '{key}' must have both 'ds_name' and "
                f"'project_name' (one published source per entry)."
            )
        refs.append(DatasourceRef(key=key, ds_name=ds_name, project_name=project_name))

    if not refs:
        raise VdsError(
            "datasources.json lists no data sources (only comment keys). Add one entry "
            "per published source: {\"ds_1\": {\"ds_name\": ..., \"project_name\": ...}}."
        )
    return refs


# --- Slug / type helpers -----------------------------------------------------

def slugify(ds_name: str) -> str:
    """Turn a data-source name into the ``data/<slug>.csv`` base name (CONTRACT.md §3.2).

    Lowercases, collapses every run of non-alphanumeric characters to a single ``_``,
    and strips leading/trailing underscores. ``"Regional Sales"`` -> ``"regional_sales"``.

    Args:
        ds_name: The published data source's name.

    Returns:
        A filesystem-safe slug (without the ``.csv`` extension).

    Raises:
        VdsError: If the name has no alphanumeric characters to slugify.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", ds_name.lower()).strip("_")
    if not slug:
        raise VdsError(
            f"Data source name '{ds_name}' has no alphanumeric characters to build a "
            f"CSV filename from."
        )
    return slug


def vds_type_to_model_type(vds_data_type: str) -> str:
    """Map a VDS field ``dataType`` to data.py's TYPES vocabulary (default ``string``).

    Args:
        vds_data_type: The ``dataType`` from VDS metadata (e.g. ``"INTEGER"``).

    Returns:
        One of data.py's TYPES; ``"string"`` for any unrecognized VDS type.
    """
    return _VDS_TYPE_MAP.get((vds_data_type or "").upper(), _FALLBACK_MODEL_TYPE)


# --- Metadata field shape ----------------------------------------------------

@dataclass(frozen=True)
class FieldMeta:
    """One queryable field as reported by VDS ``read-metadata``.

    Attributes:
        caption: The field caption (``fieldCaption``) — the header used in the pulled
            CSV and the query body. Authoritative field name.
        model_type: The VDS ``dataType`` mapped onto data.py's TYPES.
        description: The field description from metadata (``""`` when VDS gives none;
            the model fills these in afterward, as for the csv route).
    """

    caption: str
    model_type: str
    description: str


# --- HTTP helpers ------------------------------------------------------------

def _post_json(session: requests.Session, url: str, *, headers: dict, payload: dict,
               what: str) -> dict:
    """POST JSON and return the decoded body, mapping any failure to :class:`VdsError`.

    Args:
        session: The HTTP session (injectable for tests).
        url: The absolute URL to POST to.
        headers: Request headers.
        payload: The JSON request body.
        what: A short description of the call for error messages (e.g. ``"sign-in"``).

    Returns:
        The decoded JSON response body.

    Raises:
        VdsError: On a connection error, a non-2xx status, or a non-JSON body.
    """
    try:
        response = session.post(url, json=payload, headers=headers, timeout=_HTTP_TIMEOUT)
    except requests.RequestException as error:
        raise VdsError(f"{what} failed to reach {url}: {error}.")
    return _decode(response, what)


def _decode(response: requests.Response, what: str) -> dict:
    """Validate an HTTP status and decode a JSON body, raising :class:`VdsError`.

    Args:
        response: The HTTP response to validate.
        what: A short description of the call for error messages.

    Returns:
        The decoded JSON response body.

    Raises:
        VdsError: On a non-2xx status (with the server's message) or a non-JSON body.
    """
    if not response.ok:
        raise VdsError(
            f"{what} returned HTTP {response.status_code}: "
            f"{response.text.strip()[:500] or '(no body)'}."
        )
    try:
        return response.json()
    except ValueError:
        raise VdsError(f"{what} returned a non-JSON response: {response.text[:200]!r}.")


def make_session() -> requests.Session:
    """requests.Session: A fresh HTTP session for a pull (factory, for easy mocking)."""
    return requests.Session()


# --- Sign-in -----------------------------------------------------------------

def sign_in(conn: TableauConnection, session: requests.Session) -> tuple[str, str]:
    """Sign in with the Personal Access Token and return ``(auth_token, site_id)``.

    The credentials token returned here is reused for both the LUID lookup and the VDS
    calls via the ``X-Tableau-Auth`` header (CONTRACT.md §3.2).

    Args:
        conn: The resolved Tableau connection.
        session: The HTTP session (injectable for tests).

    Returns:
        A ``(auth_token, site_id)`` tuple.

    Raises:
        VdsError: On a connection failure, bad credentials, or an unexpected response
            shape (e.g. a wrong server URL or a PAT that has been revoked).
    """
    payload = {
        "credentials": {
            "personalAccessTokenName": conn.pat_name,
            "personalAccessTokenSecret": conn.pat_secret,
            "site": {"contentUrl": conn.site},
        }
    }
    body = _post_json(
        session,
        conn.rest_url("/auth/signin"),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        payload=payload,
        what="Tableau sign-in",
    )
    credentials = body.get("credentials", {})
    token = credentials.get("token")
    site_id = credentials.get("site", {}).get("id")
    if not token or not site_id:
        raise VdsError(
            "Tableau sign-in succeeded but returned no token/site id. Check "
            "TABLEAU_SERVER, TABLEAU_SITE, and the PAT name/secret in your .env."
        )
    logger.info(f"Signed in to {conn.server} (site id {site_id}).")
    return token, site_id


def _auth_headers(token: str) -> dict:
    """dict: The headers carrying the credentials token for REST + VDS calls."""
    return {
        "X-Tableau-Auth": token,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


# --- LUID resolution ---------------------------------------------------------

def resolve_luid(conn: TableauConnection, token: str, site_id: str,
                 ds_name: str, project_name: str, session: requests.Session) -> str:
    """Resolve a published source's LUID from its ``(ds_name, project_name)``.

    Queries the REST ``Query Data Sources`` endpoint filtered by name, then matches the
    project. A source that does not resolve is most often **embedded** (VDS cannot see
    embedded sources) or simply absent, so the error steers the analyst to export the
    data to CSV and use Route 1 instead (CONTRACT.md §3.2).

    Args:
        conn: The resolved Tableau connection.
        token: The credentials token from :func:`sign_in`.
        site_id: The site id from :func:`sign_in`.
        ds_name: The published data source's name.
        project_name: The project the source should live in.
        session: The HTTP session (injectable for tests).

    Returns:
        The data source's LUID (its REST ``id``).

    Raises:
        VdsError: On a request failure, or when no published source matches both the
            name and the project.
    """
    # Server-side name filter narrows the result; we still match the project ourselves
    # because (name, project) is what makes the source unique.
    url = conn.rest_url(f"/sites/{site_id}/datasources")
    params = {"filter": f"name:eq:{ds_name}", "pageSize": "100"}
    try:
        response = session.get(url, params=params, headers=_auth_headers(token),
                               timeout=_HTTP_TIMEOUT)
    except requests.RequestException as error:
        raise VdsError(f"Querying data sources failed to reach {url}: {error}.")
    body = _decode(response, "Query Data Sources")

    candidates = body.get("datasources", {}).get("datasource", [])
    for candidate in candidates:
        if candidate.get("name") == ds_name and \
                candidate.get("project", {}).get("name") == project_name:
            luid = candidate.get("id")
            if not luid:
                break
            logger.info(f"Resolved '{ds_name}' in '{project_name}' to LUID {luid}.")
            return luid

    raise VdsError(
        f"No PUBLISHED data source named '{ds_name}' in project '{project_name}' "
        f"resolved on this site. Check the name/project, or note that VDS sees "
        f"PUBLISHED sources only — if this is an EMBEDDED source (bundled inside a "
        f"workbook) it cannot be queried; export the data to CSV from Tableau and drop "
        f"the CSV(s) in data/ to use the csv route instead."
    )


# --- VDS calls ---------------------------------------------------------------

def read_metadata(conn: TableauConnection, token: str, luid: str,
                  session: requests.Session) -> list[FieldMeta]:
    """Read the queryable fields of a data source via VDS ``read-metadata``.

    The returned fields are authoritative for names, types, and descriptions
    (CONTRACT.md §3.2). Only these queryable fields are pulled; hidden / non-queryable
    fields are absent here and therefore skipped downstream.

    Args:
        conn: The resolved Tableau connection.
        token: The credentials token from :func:`sign_in`.
        luid: The data source LUID from :func:`resolve_luid`.
        session: The HTTP session (injectable for tests).

    Returns:
        One :class:`FieldMeta` per queryable field, in metadata order.

    Raises:
        VdsError: On a request failure, or when metadata reports no queryable fields
            (commonly the source's **API Access** capability is off).
    """
    body = _post_json(
        session,
        conn.vds_url("read-metadata"),
        headers=_auth_headers(token),
        payload={"datasource": {"datasourceLuid": luid}},
        what="VDS read-metadata",
    )
    raw_fields = body.get("data", [])
    fields: list[FieldMeta] = []
    for raw in raw_fields:
        caption = (raw.get("fieldCaption") or "").strip()
        if not caption:
            continue  # a field with no caption cannot be queried or used as a header
        fields.append(FieldMeta(
            caption=caption,
            model_type=vds_type_to_model_type(raw.get("dataType", "")),
            description=(raw.get("description") or "").strip(),
        ))

    if not fields:
        raise VdsError(
            f"VDS read-metadata returned no queryable fields for LUID {luid}. Enable the "
            f"data source's 'API Access' capability in Tableau (required for VDS), then "
            f"re-run."
        )
    return fields


def query_rows(conn: TableauConnection, token: str, luid: str,
               field_captions: list[str], row_limit: int,
               session: requests.Session) -> list[dict]:
    """Pull a capped sample of the given fields via VDS ``query-datasource``.

    Sends exactly the body CONTRACT.md §3.2 specifies. ``row_limit`` caps the rows
    returned to us, not what VDS reads from the underlying source.

    Args:
        conn: The resolved Tableau connection.
        token: The credentials token from :func:`sign_in`.
        luid: The data source LUID from :func:`resolve_luid`.
        field_captions: The captions to pull (all queryable fields, from metadata).
        row_limit: The maximum number of rows to return.
        session: The HTTP session (injectable for tests).

    Returns:
        The sampled rows as dicts keyed by field caption.

    Raises:
        VdsError: On a request failure, or when the query returns zero rows (no silent
            fallback — the analyst fixes the source or uses Route 1).
    """
    payload = {
        "datasource": {"datasourceLuid": luid},
        "query": {"fields": [{"fieldCaption": caption} for caption in field_captions]},
        "options": {"rowLimit": row_limit},
    }
    body = _post_json(
        session,
        conn.vds_url("query-datasource"),
        headers=_auth_headers(token),
        payload=payload,
        what="VDS query-datasource",
    )
    rows = body.get("data", [])
    if not rows:
        raise VdsError(
            f"VDS query-datasource returned zero rows for LUID {luid}. The source may be "
            f"empty or filtered to nothing; there is no synthesized-data fallback "
            f"(CONTRACT.md §3.2)."
        )
    return rows
