"""The dashboard layout engine of tableau-build (CONTRACT.md step 8).

The spec's ``## Layout`` container tree is the geometry the analyst approved the mock at, so
this module turns it into the workbook's zone tree *by construction* rather than by an
agent's eyeballing: percentage ``size`` values map deterministically into Tableau's
100,000 x 100,000 coordinate space at the mock's canvas dimensions, nested ``vert``/``horz``
containers become ``layout-flow`` zones, and every zone gets a sequential id, a
``friendly-name``, and ``<zone-style>`` as its **last** child (Tableau reads that element
positionally - anything after it is ignored).

The module is pure and stdlib-only: it takes the layout tree, the canvas, and a
``{element id: Leaf}`` map of what fills each leaf, and appends zones to an element. No
filesystem, no manifest parsing - that is :mod:`twb`'s job, and it is why the contract test
can drive :func:`render_zones` directly.

Not here: the *wiring* of a filter / parameter / button zone to the field, parameter or sheet
behind it. A zone of the right type at the right place is what the layout owes; the
dashboard-level ``<datasource-dependencies>`` and button actions that make one live belong to
the features-and-actions ticket (#37).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import NamedTuple, Optional

# For DesignTokens only; :mod:`worksheet` does not import this module, so no cycle.
from worksheet import DesignTokens

#: Dashboard zones live in a 100,000 x 100,000 virtual coordinate space, whatever the canvas.
ZONE_SPACE = 100000

#: The root zone's margin in px, as Tableau writes it. Scaled by the canvas into zone units,
#: it is what insets every zone below the root.
ROOT_MARGIN_PX = 8

#: The margin on the root zone / on every other zone, in the ``<zone-style>`` block.
ROOT_MARGIN = "8"
ZONE_MARGIN = "4"

#: Height in px of a generated title text zone and of a colour legend zone.
TITLE_HEIGHT_PX = 30
LEGEND_HEIGHT_PX = 22

#: manifest ``objects[].kind`` -> the zone's ``type-v2``, for the kinds a layout can render
#: on its own: a text block and a spacer need nothing beyond their box and their text.
OBJECT_ZONE_TYPES: dict[str, str] = {
    "text": "text",
    "blank": "empty",
}

#: The other kinds. Each one's real zone type (``filter``, ``paramctrl``, ``bitmap``,
#: ``dashboard-object``, ``color``) needs a reference the manifest does not carry yet - a
#: filter's field plus the dashboard's own ``<datasource-dependencies>``, a parameter control's
#: parameter, an image's filename (which the ``.twbx`` would also have to embed), a button's
#: action, a legend's sheet and colour field - and Tableau does not treat those as optional.
#: So the layout reserves the box as an empty zone, keeping the geometry the analyst approved,
#: until the ticket that adds the wiring can emit the real zone.
DEFERRED_KINDS: frozenset[str] = frozenset({
    "filter", "parameter", "image", "button", "legend",
})

#: The zone type of a leaf nothing fills, of a deferred kind, and of an unknown one.
EMPTY_ZONE_TYPE = "empty"

#: Container orientations, and the ``friendly-name`` prefix each carries (the legacy skill's
#: ``V-``/``H-`` convention, which is what makes a deep zone tree readable while debugging).
CONTAINER_PREFIXES = {"vert": "V", "horz": "H"}

#: The root zone's friendly name.
ROOT_FRIENDLY_NAME = "Dashboard"


@dataclass(frozen=True)
class Box:
    """One zone's rectangle in the 0-100,000 coordinate space.

    Attributes:
        x: Left edge.
        y: Top edge.
        w: Width.
        h: Height.
    """

    x: int
    y: int
    w: int
    h: int


class Legend(NamedTuple):
    """The colour encoding one legend zone keys.

    Attributes:
        field: The qualified colour field reference (the zone's ``param``).
        pane_id: Which pane's encoding to show (the zone's ``pane-specification-id``).
    """

    field: str
    pane_id: str


@dataclass(frozen=True)
class Leaf:
    """What fills one leaf zone of the layout tree.

    A leaf is either a **view** (``worksheet``) or a dashboard **object** (``kind``); the
    optional title and legend are extra zones stacked around it inside the element's own box,
    so adding them never disturbs its siblings' geometry.

    Attributes:
        worksheet: The Tableau sheet name for a view zone.
        kind: The manifest object kind for a non-view zone (see :data:`OBJECT_ZONE_TYPES`).
        title: Title text; when set, a text zone is stacked above the content and the
            sheet's own title is suppressed.
        text: The text a ``text`` object zone displays.
        legend: The colour legend to stack below the content, or ``None`` for no legend.
    """

    worksheet: str = ""
    kind: str = ""
    title: str = ""
    text: str = ""
    legend: Optional[Legend] = None


def render_zone_style(parent: ET.Element, margin: str) -> None:
    """Append the four-format ``<zone-style>`` block Tableau writes on every zone.

    Args:
        parent: The zone to style.
        margin: The zone's margin, in px.
    """
    zone_style = ET.SubElement(parent, "zone-style")
    for attribute, value in (
        ("border-color", "#000000"), ("border-style", "none"), ("border-width", "0"),
        ("margin", margin),
    ):
        ET.SubElement(zone_style, "format", {"attr": attribute, "value": value})


def child_sizes(children: list) -> list[float]:
    """Return the flow-axis proportion of each child, filling in the ones that declare none.

    ``size`` values are proportions of the parent, not absolute percentages: a tree whose
    siblings sum to 90 still fills the dashboard. A child with no usable ``size`` shares
    whatever the declared ones leave - and if they leave nothing, every child splits the
    parent evenly, because a zero-height zone is a zone the analyst cannot see.

    Args:
        children: The container's child nodes.

    Returns:
        One positive proportion per child, in order.
    """
    declared: list[Optional[float]] = []
    for child in children:
        size = child.get("size") if isinstance(child, dict) else None
        usable = (
            isinstance(size, (int, float)) and not isinstance(size, bool) and size > 0
        )
        declared.append(float(size) if usable else None)

    missing = declared.count(None)
    if not missing:
        return [size for size in declared if size is not None]

    share = (100.0 - sum(size for size in declared if size is not None)) / missing
    if share <= 0:
        return [100.0 / len(children)] * len(children)
    return [share if size is None else size for size in declared]


class _ZoneWriter:
    """Renders one layout tree into zones, numbering them as it goes.

    Attributes:
        embedded: The sheet names the tree places in a zone - what the caller hides in
            ``<windows>``, since hiding a sheet no zone shows leaves Tableau no tab to
            render it on.
    """

    def __init__(
        self, canvas: dict, leaves: dict[str, Leaf], tokens: DesignTokens
    ) -> None:
        """Set up a writer for one dashboard.

        Args:
            canvas: The layout's ``canvas`` (``width`` / ``height`` in px) - what px sizes
                are converted against.
            leaves: ``{element id: Leaf}``; a leaf id that is absent renders as a blank zone.
            tokens: The parsed :class:`worksheet.DesignTokens`, for title zone styling.
        """
        self._width = max(1.0, float(canvas.get("width") or 1))
        self._height = max(1.0, float(canvas.get("height") or 1))
        self._leaves = leaves
        self._tokens = tokens
        self._last_id = 0
        self.embedded: set[str] = set()

    # --- helpers ---------------------------------------------------------------

    def _next_id(self) -> str:
        """Return the next sequential zone id."""
        self._last_id += 1
        return str(self._last_id)

    def _units_x(self, pixels: float) -> int:
        """Convert a horizontal px measure into zone units at the canvas width."""
        return round(pixels / self._width * ZONE_SPACE)

    def _units_y(self, pixels: float) -> int:
        """Convert a vertical px measure into zone units at the canvas height."""
        return round(pixels / self._height * ZONE_SPACE)

    def _zone(self, parent: ET.Element, box: Box, friendly_name: str, **attributes) -> ET.Element:
        """Append one zone with its geometry, id and friendly name.

        Args:
            parent: The zone (or ``<zones>``) to append to.
            box: The zone's rectangle.
            friendly_name: The human-readable label every zone carries.
            **attributes: Extra zone attributes (``name``, ``type-v2``, ...); a ``None``
                value is dropped, which keeps the callers free of conditionals.

        Returns:
            The new zone element.
        """
        attributes.update({
            "friendly-name": friendly_name,
            "h": str(box.h), "id": self._next_id(),
            "w": str(box.w), "x": str(box.x), "y": str(box.y),
        })
        present = {
            key.replace("_", "-"): str(value)
            for key, value in attributes.items() if value is not None
        }
        return ET.SubElement(parent, "zone", dict(sorted(present.items())))

    # --- the tree ---------------------------------------------------------------

    def render_root(self, parent: ET.Element, root: dict) -> str:
        """Render the root zone and the tree below it.

        Args:
            parent: The dashboard's ``<zones>`` element.
            root: The layout tree's ``root`` node.

        Returns:
            The root zone's id (the dashboard window's ``<active>`` target).
        """
        root_zone = self._zone(
            parent, Box(0, 0, ZONE_SPACE, ZONE_SPACE), ROOT_FRIENDLY_NAME,
            type_v2="layout-basic",
        )
        root_id = root_zone.get("id")
        inset_x, inset_y = self._units_x(ROOT_MARGIN_PX), self._units_y(ROOT_MARGIN_PX)
        self._node(
            root_zone,
            root,
            Box(inset_x, inset_y, ZONE_SPACE - 2 * inset_x, ZONE_SPACE - 2 * inset_y),
            "root",
        )
        render_zone_style(root_zone, ROOT_MARGIN)  # last child, always
        return root_id

    def _node(self, parent: ET.Element, node: object, box: Box, path: str) -> None:
        """Render one layout node - a container, a leaf, or a mapped container - into ``box``.

        Args:
            parent: The zone to nest this node's zone in.
            node: The layout node.
            box: The rectangle the node occupies.
            path: Its position in the tree (``root.2.1``), the fallback friendly name.
        """
        if not isinstance(node, dict):
            return  # validate_manifest already rejected it; keep the rest of the tree

        element_id = str(node.get("id") or "").strip()
        children = node.get("children")
        if not (isinstance(children, list) and children):
            self._leaf(parent, element_id, box, path)
            return

        orientation = "vert" if str(node.get("type", "")).strip().lower() == "vert" else "horz"
        container = self._zone(
            parent, box, f"{CONTAINER_PREFIXES[orientation]}-{element_id or path}",
            type_v2="layout-flow", param=orientation,
        )
        for index, (child, child_box) in enumerate(
            zip(children, self._divide(box, orientation, child_sizes(children))), start=1
        ):
            self._node(container, child, child_box, f"{path}.{index}")
        render_zone_style(container, ZONE_MARGIN)

    def _divide(self, box: Box, orientation: str, sizes: list[float]) -> list[Box]:
        """Split ``box`` along the flow axis into one rectangle per proportion.

        Each edge is rounded from the *cumulative* proportion, so the children tile the
        parent exactly: no rounding gap, no overlap, and the last child ends where the
        parent does.

        Args:
            box: The container's rectangle.
            orientation: ``vert`` (divide the height) or ``horz`` (divide the width).
            sizes: One positive proportion per child.

        Returns:
            One :class:`Box` per proportion, in order.
        """
        vertical = orientation == "vert"
        start, extent = (box.y, box.h) if vertical else (box.x, box.w)
        total = sum(sizes)

        boxes: list[Box] = []
        edge, consumed = start, 0.0
        for size in sizes:
            consumed += size
            end = start + round(extent * consumed / total)
            boxes.append(
                Box(box.x, edge, box.w, end - edge) if vertical
                else Box(edge, box.y, end - edge, box.h)
            )
            edge = end
        return boxes

    # --- leaves ------------------------------------------------------------------

    def _leaf(self, parent: ET.Element, element_id: str, box: Box, path: str) -> None:
        """Render one leaf element, wrapping it when it carries a title or a legend.

        Args:
            parent: The zone to nest the leaf in.
            element_id: The leaf's element id (``""`` for an unnamed node).
            box: The rectangle the element occupies.
            path: Its position in the tree, the fallback friendly name.
        """
        label = element_id or path
        leaf = self._leaves.get(element_id, Leaf())
        title_height = self._units_y(TITLE_HEIGHT_PX) if leaf.title else 0
        legend_height = self._units_y(LEGEND_HEIGHT_PX) if leaf.legend else 0

        if not (title_height or legend_height):
            self._content(parent, leaf, box, label)
            return

        # The extra zones stack inside the element's own box, so its siblings are untouched.
        wrapper = self._zone(
            parent, box, f"{CONTAINER_PREFIXES['vert']}-{label}",
            type_v2="layout-flow", param="vert",
        )
        # A box too short for both fixed zones is squeezed, never overflowed: a zone written
        # past the wrapper's bottom edge would overlap the sibling below it.
        title_height = min(title_height, box.h)
        legend_height = min(legend_height, box.h - title_height)
        content_height = box.h - title_height - legend_height
        cursor = box.y
        if title_height:
            self._title(wrapper, leaf.title, Box(box.x, cursor, box.w, title_height), label)
            cursor += title_height
        self._content(
            wrapper, leaf, Box(box.x, cursor, box.w, content_height), label,
            show_title=False,
        )
        cursor += content_height
        if legend_height:
            self._legend(
                wrapper, leaf, Box(box.x, cursor, box.w, legend_height), label
            )
        render_zone_style(wrapper, ZONE_MARGIN)

    def _content(
        self, parent: ET.Element, leaf: Leaf, box: Box, label: str, show_title: bool = True
    ) -> None:
        """Render the leaf's own zone: a sheet zone, an object zone, or a blank.

        Args:
            parent: The zone to append to.
            leaf: What fills the element.
            box: The rectangle for the content itself.
            label: The zone's friendly name.
            show_title: False when a generated title zone already labels the element, which
                is what suppresses the sheet's own duplicate title.
        """
        if leaf.worksheet:
            # A sheet zone is identified by its 'name' and carries no type-v2.
            zone = self._zone(
                parent, box, label, name=leaf.worksheet,
                show_title=None if show_title else "false",
            )
            self.embedded.add(leaf.worksheet)
        else:
            kind = leaf.kind.strip().lower()
            zone = self._zone(
                parent, box, label, type_v2=OBJECT_ZONE_TYPES.get(kind, EMPTY_ZONE_TYPE),
            )
            if kind == "text" and leaf.text:
                self._formatted_text(zone, leaf.text, bold=False)
        render_zone_style(zone, ZONE_MARGIN)

    def _title(self, parent: ET.Element, text: str, box: Box, label: str) -> None:
        """Render a fixed-height text zone carrying the element's title."""
        zone = self._zone(
            parent, box, f"{label} title", type_v2="text",
            fixed_size=str(TITLE_HEIGHT_PX), is_fixed="true",
        )
        self._formatted_text(zone, text, bold=True)
        render_zone_style(zone, ZONE_MARGIN)

    def _legend(self, parent: ET.Element, leaf: Leaf, box: Box, label: str) -> None:
        """Render a fixed-height colour legend zone keying the sheet's colour encoding."""
        legend = leaf.legend
        zone = self._zone(
            parent, box, f"{label} legend", type_v2="color",
            fixed_size=str(LEGEND_HEIGHT_PX), is_fixed="true", leg_item_layout="horz",
            name=leaf.worksheet or None, pane_specification_id=legend.pane_id,
            param=legend.field, show_title="false",
        )
        render_zone_style(zone, ZONE_MARGIN)

    def _formatted_text(self, zone: ET.Element, text: str, bold: bool) -> None:
        """Append a single-run ``<formatted-text>`` block styled from the design tokens."""
        attributes = {
            "fontname": self._tokens.font_family,
            "fontsize": str(self._tokens.title_size),
            "fontcolor": self._tokens.title_color.lower(),
        }
        if bold:
            attributes["bold"] = "true"
        run = ET.SubElement(
            ET.SubElement(zone, "formatted-text"), "run", dict(sorted(attributes.items()))
        )
        run.text = text


def render_zones(
    parent: ET.Element,
    root: dict,
    canvas: dict,
    leaves: dict[str, Leaf],
    tokens: DesignTokens,
) -> tuple[str, set[str]]:
    """Render a layout tree into a dashboard's ``<zones>``.

    Args:
        parent: The dashboard's ``<zones>`` element.
        root: The layout tree's ``root`` node (``{"type": ..., "children": [...]}``).
        canvas: The layout's ``canvas`` - the px dimensions px sizes are scaled against.
        leaves: ``{element id: Leaf}``, what fills each leaf zone.
        tokens: The parsed :class:`worksheet.DesignTokens`, for title zone styling.

    Returns:
        ``(root zone id, embedded sheet names)``. The root id is the dashboard window's
        ``<active>`` target; the sheet names are the ones the caller hides in ``<windows>``.
    """
    writer = _ZoneWriter(canvas, leaves, tokens)
    return writer.render_root(parent, root), writer.embedded
