"""Spec-conformance validator: does the built workbook agree with the manifest?

The third validator of the build gate, and the only one that reads both sides. The other
two look at the workbook alone - :mod:`validate_twb` asks whether the XML is internally
consistent, :mod:`validate_twb_xsd` whether it matches the schema - so a workbook that
dropped a worksheet, or a zone the analyst approved in the mock, passes both of them
happily: what is missing is missing consistently. The manifest is the machine-readable
translation of the approved ``IMPLEMENTATION-SPEC.md``, so comparing the two is what proves
the analyst is being handed the dashboard they signed off on.

Four checks (issue #38):

1. Every element id the manifest's layout places became a zone.
2. Every zone that names a sheet names one that exists.
3. Every worksheet the manifest declares exists **and** has a ``<window>``.
4. Every sheet a dashboard embeds has a ``<viewpoint>`` in the dashboard's window.

Plus the **unsupported-construct policy** (:func:`unsupported_notes`): a construct the
builder has no template for reserves its box and is *named*, with the two ways forward the
analyst is owed - a reference ``.twb`` saved from Tableau Desktop, or one hand-written block.
A silently empty zone is the failure mode this exists to prevent.

Pure and stdlib-only: it takes a parsed ``<workbook>`` element and the manifest document, so
the contract test drives it directly.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from manifest import placed_layout_ids
from zones import CONTAINER_PREFIXES, DEFERRED_KINDS

#: A leaf zone carries its element id as its ``friendly-name`` verbatim; a container (and the
#: wrapper of a titled/legended leaf) prefixes it with the orientation letter. Those are the
#: only forms an id appears in, so they are the names an id is looked up under.
_ID_PREFIXES: tuple[str, ...] = ("",) + tuple(
    f"{prefix}-" for prefix in CONTAINER_PREFIXES.values()
)


def _zone_friendly_names(root: ET.Element) -> set[str]:
    """Return every ``friendly-name`` in every dashboard's zone tree."""
    return {
        name
        for zone in root.iterfind(".//dashboards/dashboard/zones//zone")
        for name in [zone.get("friendly-name")]
        if name
    }


def _referenced_sheet_names(root: ET.Element) -> set[str]:
    """Return every sheet name the dashboards' zones reference.

    A sheet zone, a filter card and a legend zone all name the sheet they belong to; nothing
    else in a zone tree carries ``name``.
    """
    return {
        name
        for zone in root.iterfind(".//dashboards/dashboard/zones//zone")
        for name in [zone.get("name")]
        if name
    }


def _sheet_zone_names(root: ET.Element) -> set[str]:
    """Return the sheet names actually *drawn* by a zone.

    A sheet zone is the one identified by its ``name`` alone (:mod:`zones` gives it no
    ``type-v2``); a filter card or a legend also names its sheet but does not render it, so
    counting those as placement would pass a chart whose own zone went missing.
    """
    return {
        name
        for zone in root.iterfind(".//dashboards/dashboard/zones//zone")
        for name in [zone.get("name")]
        if name and zone.get("type-v2") is None
    }


def conformance_errors(root: ET.Element, document: dict) -> list[str]:
    """Check a built workbook against the manifest it was built from.

    Args:
        root: The parsed ``<workbook>`` element.
        document: The validated build manifest.

    Returns:
        One message per disagreement, each naming the offending sheet or element id; empty
        when the workbook carries everything the manifest asked for.
    """
    errors: list[str] = []

    sheet_names = {
        name for sheet in root.iterfind("worksheets/worksheet")
        for name in [sheet.get("name")] if name
    }
    window_names = {
        name for window in root.iterfind("windows/window")
        for name in [window.get("name")]
        if name and window.get("class") == "worksheet"
    }

    # 1. Every zone the analyst approved is in the workbook.
    friendly_names = _zone_friendly_names(root)
    for element_id in sorted(placed_layout_ids(document.get("layout"))):
        if not any(f"{prefix}{element_id}" in friendly_names for prefix in _ID_PREFIXES):
            errors.append(
                f"layout element '{element_id}' has no zone in the built dashboard - the "
                f"analyst approved it in the mock, so the workbook must place it."
            )

    # 2. No zone points at a sheet that is not there (Tableau renders an error tile).
    referenced = _referenced_sheet_names(root)
    drawn = _sheet_zone_names(root)
    for name in sorted(referenced - sheet_names):
        errors.append(
            f"dashboard zone references sheet '{name}', which is not a "
            f"<worksheet> in the workbook."
        )

    # 3. Every declared worksheet exists, is placed, and has the window that gives it a tab.
    # The placement check is what gives (1) teeth for a view element: a friendly-name survives
    # on the wrapper of a titled or legended leaf even if the sheet zone inside it is gone.
    for entry in document.get("worksheets") or []:
        name = str((entry or {}).get("name", "")).strip()
        if not name:
            continue
        if name not in sheet_names:
            errors.append(
                f"manifest worksheet '{name}' was not built - no "
                f"<worksheet name='{name}'> in the workbook."
            )
            continue
        if name not in window_names:
            errors.append(
                f"sheet '{name}' has no <window name='{name}'> - Tableau renders no tab "
                f"for it, so it cannot be reviewed or repaired in Desktop."
            )
        element_id = str(entry.get("element_id", "")).strip()
        if element_id and name not in drawn:
            errors.append(
                f"sheet '{name}' fills layout element '{element_id}' but no dashboard zone "
                f"embeds it - the element's box would render blank."
            )

    # 4. An embedded sheet with no viewpoint opens at the wrong zoom in the dashboard.
    for dashboard in root.iterfind("dashboards/dashboard"):
        dashboard_name = dashboard.get("name", "")
        window = root.find(f"windows/window[@name='{dashboard_name}']")
        viewpoints = {
            name for viewpoint in (
                [] if window is None else window.iterfind("viewpoints/viewpoint")
            )
            for name in [viewpoint.get("name")] if name
        }
        for name in sorted(
            {zone.get("name") for zone in dashboard.iterfind("zones//zone")
             if zone.get("name")} & sheet_names - viewpoints
        ):
            errors.append(
                f"dashboard '{dashboard_name}' embeds sheet '{name}' but its window has no "
                f"<viewpoint name='{name}'> - the sheet opens at the wrong fit."
            )

    return errors


def unsupported_notes(document: dict) -> list[str]:
    """Name every construct the builder has no template for, and the two ways forward.

    The builder refuses the *piece*, not the workbook: the zone keeps its box so the
    approved geometry holds, and everything else is built. What must never happen is the
    analyst discovering the gap by finding an empty rectangle - hence one note per gap, with
    the offer the policy owes them (issue #38).

    Args:
        document: The build manifest.

    Returns:
        One plain-ASCII note per unsupported construct; empty when every construct in the
        manifest has a template.
    """
    notes: list[str] = []
    for entry in document.get("objects") or []:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind", "")).strip().lower()
        if kind not in DEFERRED_KINDS:
            continue
        element_id = str(entry.get("element_id", "")).strip() or "<unnamed>"
        notes.append(
            f"no template for a '{kind}' object ('{element_id}'): its box is reserved as an "
            f"empty zone so the approved geometry holds, and nothing is drawn in it. To "
            f"close the gap, build one in Tableau Desktop and save it as a reference .twb "
            f"for me to read - or say the word and I can hand-write this one block and "
            f"validate it."
        )
    return notes
