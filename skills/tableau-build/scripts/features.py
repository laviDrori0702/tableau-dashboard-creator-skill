"""Parameters, dashboard actions and Dynamic Zone Visibility for tableau-build (step 8).

:mod:`twb` owns the workbook shell, :mod:`worksheet` what goes inside a sheet, :mod:`zones`
the dashboard's geometry. This module owns the three constructs that make a built dashboard
*interactive* rather than merely correct, and that live in their own workbook-level elements:

* the **``Parameters`` datasource** - the inline, connectionless datasource every parameter is
  a column of (``[Parameters].[Top N]``);
* the workbook's **``<actions>``** block - filter and highlight actions (``<action>``) and
  parameter actions (``<edit-parameter-action>``), bound to real sheet names;
* the **``<datagraph>``** behind Dynamic Zone Visibility - the node/edge graph that wires a
  boolean field to a zone's visibility.

The module is pure and stdlib-only: elements in, elements out. It deliberately does **not**
generate ids: an action's ``name`` and a zone's id come from the caller, so every generated id
in the workbook stays hash-derived from one place (:mod:`twb`) and a rebuild is byte-identical.
"""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional

# --- Parameters ----------------------------------------------------------------

#: The name (and caption) of the inline datasource every parameter lives in. Not a federated
#: id: ``[Parameters]`` is the literal name Tableau references a parameter through.
PARAMETERS_DATASOURCE = "Parameters"

#: manifest ``data_type`` -> the parameter column's ``type`` attribute.
#: ``manifest.PARAMETER_TYPES`` reads this table, so an undeclarable type fails validation.
PARAMETER_TYPES: dict[str, str] = {
    "string": "nominal",
    "integer": "quantitative",
    "real": "quantitative",
    "boolean": "nominal",
    "date": "ordinal",
    "datetime": "ordinal",
}

#: A parameter is always a measure, whatever its datatype - attested for both a string and an
#: integer parameter, and it is what puts the control in the Parameters section of the pane.
PARAMETER_ROLE = "measure"

#: Bare-value datatypes; everything else needs delimiters (see :func:`parameter_literal`).
_BARE_VALUE_TYPES = frozenset({"integer", "real"})

#: The type tag a parameter action's ``<clear-option value>`` carries, per parameter data
#: type. The leading letter is the data type and only a string adds the ``LROOT:`` segment;
#: the value that follows is *undelimited* (``All``, not ``"All"``). Read off Desktop-saved
#: workbooks - see ``references/snippets/dashboard/CLEAR-OPTION-ATTESTATION.md`` for the
#: exact elements and where each came from. ``real``, ``date`` and ``datetime`` are absent
#: because no Desktop workbook writing one has been seen; ``manifest`` rejects a parameter
#: action targeting them rather than building one that cannot reset (issue #49).
CLEAR_VALUE_TAGS: dict[str, str] = {
    "string": "s:LROOT:",
    "integer": "i:",
    "boolean": "b:",
}

#: What Desktop writes in a ``do-nothing`` clear-option, where the value is never read.
CLEAR_VALUE_UNUSED = "s:LROOT:"


def parameter_literal(value: object, data_type: str) -> str:
    """Render a parameter value the way Tableau writes it in ``value`` and its calculation.

    Args:
        value: The manifest's value (a string, number or boolean).
        data_type: The parameter's manifest data type.

    Returns:
        The literal: ``"on"`` for a string (quotes included), ``10`` for a number,
        ``true``/``false`` for a boolean, and ``#2024-01-01#`` for a date.
    """
    if data_type == "boolean":
        return "true" if value in (True, "true", "True", 1) else "false"
    if data_type in _BARE_VALUE_TYPES:
        return str(value)
    text = str(value)
    if data_type in {"date", "datetime"}:
        return text if text.startswith("#") else f"#{text}#"
    return text if text.startswith('"') else f'"{text}"'


def serialize_clear_value(current: object, data_type: str) -> str:
    """Serialize a parameter action's reset value the way Desktop writes it.

    Args:
        current: The value the parameter goes back to when the selection is cleared.
        data_type: The parameter's manifest data type.

    Returns:
        The ``<clear-option value>`` text (``s:LROOT:All``, ``i:10``, ``b:false``), or ``""``
        for a data type with no attested tag - which leaves the action a ``do-nothing``.
    """
    tag = CLEAR_VALUE_TAGS.get(data_type)
    if tag is None:
        return ""
    # A string's value goes in bare; a number's and a boolean's are already bare literals.
    body = current if data_type == "string" else parameter_literal(current, data_type)
    return f"{tag}{body}"


@dataclass(frozen=True)
class Parameter:
    """One resolved parameter: its literal current value and its domain.

    Attributes:
        name: The parameter's name - also its column name, so a formula reads
            ``[Parameters].[Top N]`` rather than ``[Parameters].[Parameter 1]``.
        data_type: The manifest data type (a key of :data:`PARAMETER_TYPES`).
        value: The current value, already rendered as a Tableau literal.
        clear_value: The serialized value a parameter action resets to when the selection is
            cleared (see :func:`serialize_clear_value`); ``""`` for a parameter whose type has
            no attested serialization, which keeps the value the last click put there.
        members: A list domain's allowed values, as literals; empty for the other domains.
        bounds: A range domain's ``(min, max, granularity)`` literals, or ``None``.
        number_format: A ``default-format`` pattern for the control, or ``""``.
    """

    name: str
    data_type: str
    value: str
    clear_value: str = ""
    members: tuple[str, ...] = ()
    bounds: Optional[tuple[str, str, str]] = None
    number_format: str = ""

    @property
    def column_name(self) -> str:
        """str: The bracketed column name (``[Top N]``)."""
        return f"[{self.name}]"

    @property
    def reference(self) -> str:
        """str: The qualified reference a formula or a control uses."""
        return f"[{PARAMETERS_DATASOURCE}].{self.column_name}"

    @property
    def domain_type(self) -> str:
        """str: The ``param-domain-type``: a member list, a range, or any value."""
        if self.members:
            return "list"
        return "range" if self.bounds else "any"


def plan_parameters(entries: object) -> list[Parameter]:
    """Resolve the manifest's ``parameters`` list into :class:`Parameter`s.

    Args:
        entries: The manifest's ``parameters`` value.

    Returns:
        One :class:`Parameter` per named entry, in manifest order. An entry
        ``manifest.validate_manifest`` would have rejected (no name, unknown data type, no
        current value) is skipped rather than guessed at.
    """
    parameters: list[Parameter] = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        data_type = str(entry.get("data_type", "")).strip().lower()
        if not name or data_type not in PARAMETER_TYPES:
            continue

        values = entry.get("values")
        members = (
            tuple(parameter_literal(value, data_type) for value in values)
            if isinstance(values, list) and values else ()
        )
        bounds = None
        span = entry.get("range")
        if not members and isinstance(span, dict):
            minimum, maximum = span.get("min"), span.get("max")
            if minimum is not None and maximum is not None:
                # No step means "any value in the range"; Tableau writes the granularity as
                # 1, which is the smallest step its slider offers for an integer.
                step = span.get("step", 1)
                bounds = (
                    parameter_literal(minimum, data_type),
                    parameter_literal(maximum, data_type),
                    parameter_literal(step, data_type),
                )

        current = entry.get("current_value")
        if current is None:
            continue  # validate_manifest requires it; guessing would hide a bad manifest
        parameters.append(Parameter(
            name=name,
            data_type=data_type,
            value=parameter_literal(current, data_type),
            clear_value=serialize_clear_value(current, data_type),
            members=members,
            bounds=bounds,
            number_format=str(entry.get("format", "")).strip(),
        ))
    return parameters


def render_parameter_column(
    parent: ET.Element, parameter: Parameter, with_domain: bool = True
) -> None:
    """Render one parameter's ``<column>``.

    Args:
        parent: The element to append the column to (the ``Parameters`` datasource, the
            dashboard's dependencies, or a worksheet's).
        parameter: The resolved parameter.
        with_domain: Emit the ``<members>`` / ``<range>`` child. A worksheet declares the
            parameter only to resolve a calculation, and Tableau writes no domain there.
    """
    attributes = {
        "caption": parameter.name,
        "datatype": parameter.data_type,
        "name": parameter.column_name,
        "param-domain-type": parameter.domain_type,
        "role": PARAMETER_ROLE,
        "type": PARAMETER_TYPES[parameter.data_type],
        "value": parameter.value,
    }
    if parameter.number_format:
        attributes["default-format"] = parameter.number_format
    column = ET.SubElement(parent, "column", dict(sorted(attributes.items())))
    # The calculation *is* the current value: a parameter is a constant the viewer edits.
    ET.SubElement(column, "calculation", {"class": "tableau", "formula": parameter.value})
    if not with_domain:
        return
    if parameter.members:
        members = ET.SubElement(column, "members")
        for member in parameter.members:
            ET.SubElement(members, "member", {"value": member})
    elif parameter.bounds:
        minimum, maximum, granularity = parameter.bounds
        ET.SubElement(
            column, "range", {"granularity": granularity, "max": maximum, "min": minimum}
        )


def render_parameters_datasource(
    parent: ET.Element, parameters: list[Parameter], version: str
) -> None:
    """Render the inline ``Parameters`` datasource, one column per parameter.

    It carries no connection (``hasconnection='false'``) and must be the **last** datasource:
    Tableau resolves ``[Parameters].[...]`` references from it after the real datasources.

    Args:
        parent: The workbook's ``<datasources>`` element.
        parameters: The resolved parameters (nothing is emitted for an empty list).
        version: The workbook document version.
    """
    if not parameters:
        return
    datasource = ET.SubElement(parent, "datasource", {
        "hasconnection": "false",
        "inline": "true",
        "name": PARAMETERS_DATASOURCE,
        "version": version,
    })
    ET.SubElement(datasource, "aliases", {"enabled": "yes"})
    for parameter in parameters:
        render_parameter_column(datasource, parameter)


# --- Actions -------------------------------------------------------------------

#: manifest ``run_on`` -> the ``<activation>`` type. ``manifest`` reads this table.
ACTIVATIONS: dict[str, str] = {"select": "on-select", "hover": "on-hover"}

#: The Tableau command behind each sheet-to-sheet action type.
ACTION_COMMANDS: dict[str, str] = {"filter": "tsc:tsl-filter", "highlight": "tsc:brush"}


@dataclass(frozen=True)
class SheetAction:
    """One filter or highlight action, from one sheet to one other sheet.

    One action per *target* rather than one action targeting the whole dashboard: targeting
    the dashboard filters every sheet on it, including ones the manifest never listed.

    Attributes:
        name: The action's XML ``name`` (``[Action1_<32 hex>]``), generated by the caller.
        caption: The action's display name.
        kind: ``filter`` or ``highlight`` (a key of :data:`ACTION_COMMANDS`).
        source: The sheet whose marks the viewer selects.
        target: The sheet the action acts on.
        activation: ``on-select`` or ``on-hover``.
    """

    name: str
    caption: str
    kind: str
    source: str
    target: str
    activation: str = "on-select"


@dataclass(frozen=True)
class ParameterAction:
    """One parameter action: selecting a mark writes a field's value into a parameter.

    Attributes:
        name: The action's XML ``name``, generated by the caller.
        caption: The action's display name.
        source: The sheet whose marks the viewer selects.
        source_field: The qualified column-instance the value is read from.
        target_parameter: The qualified parameter the value is written to.
        clear_value: The serialized value the parameter is reset to when the selection is
            cleared (:func:`serialize_clear_value`); ``""`` keeps the last clicked value.
        activation: ``on-select`` or ``on-hover``.
    """

    name: str
    caption: str
    source: str
    source_field: str
    target_parameter: str
    clear_value: str = ""
    activation: str = "on-select"


#: The document-format flags a workbook with an ``<edit-parameter-action>`` carries. Tableau
#: gates the element on them: without ``ParameterAction`` Desktop loads the workbook against a
#: schema where ``edit-parameter-action`` is not declared at all and refuses it outright
#: ("no declaration found for element"), even though the 2026.1 XSD accepts it. The second flag
#: goes with the ``<clear-option>`` child. Both verified against the Desktop 2025.1.10-saved
#: workbook this skill keeps at ``references/snippets/dashboard/parameter-action.twb``.
PARAMETER_ACTION_FORMAT_FLAGS: tuple[str, ...] = (
    "ParameterAction",
    "ParameterActionClearSelection",
)


def render_actions(
    parent: ET.Element,
    dashboard_name: str,
    sheet_actions: list[SheetAction],
    parameter_actions: list[ParameterAction],
) -> None:
    """Render the workbook's ``<actions>`` block.

    Nothing is emitted when both lists are empty: the schema requires at least one child, so
    an empty ``<actions/>`` is a workbook Tableau refuses rather than a workbook with no
    interactivity. Sheet actions come first and parameter actions last - the XSD's order.

    Args:
        parent: The ``<workbook>`` element.
        dashboard_name: The dashboard every action's source sits on.
        sheet_actions: The filter / highlight actions.
        parameter_actions: The parameter actions.
    """
    if not (sheet_actions or parameter_actions):
        return
    actions = ET.SubElement(parent, "actions")

    for action in sheet_actions:
        element = ET.SubElement(
            actions, "action", {"caption": action.caption, "name": action.name}
        )
        ET.SubElement(
            element, "activation", {"auto-clear": "true", "type": action.activation}
        )
        ET.SubElement(element, "source", {
            "dashboard": dashboard_name, "type": "sheet", "worksheet": action.source,
        })
        command = ET.SubElement(
            element, "command", {"command": ACTION_COMMANDS[action.kind]}
        )
        # 'exclude' keeps the action off its own source; the field parameter says which
        # fields are matched - all the ones the two sheets share.
        matched = "special-fields" if action.kind == "filter" else "field-captions"
        for name, value in (
            ("exclude", action.source), (matched, "all"), ("target", action.target),
        ):
            ET.SubElement(command, "param", {"name": name, "value": value})

    for action in parameter_actions:
        element = ET.SubElement(
            actions, "edit-parameter-action",
            {"caption": action.caption, "name": action.name},
        )
        ET.SubElement(element, "activation", {"type": action.activation})
        ET.SubElement(element, "source", {
            "dashboard": dashboard_name, "type": "sheet", "worksheet": action.source,
        })
        # ATTR() of the selected marks' field. Clearing the selection puts the parameter back
        # to its opening value, so a panel the parameter reveals hides itself again and the
        # viewer cannot get stuck in a state only a parameter control could undo.
        ET.SubElement(element, "agg-type", {"type": "attr"})
        ET.SubElement(element, "clear-option", (
            {"type": "assign-fixed-value", "value": action.clear_value}
            if action.clear_value else {"type": "do-nothing", "value": CLEAR_VALUE_UNUSED}
        ))
        params = ET.SubElement(element, "params")
        for name, value in (
            ("source-field", action.source_field),
            ("target-parameter", action.target_parameter),
        ):
            ET.SubElement(params, "param", {"name": name, "value": value})


# --- Dynamic Zone Visibility ----------------------------------------------------

#: The document-format flags a workbook with a ``<datagraph>`` carries. Emitted only when DZV
#: is present, sorted into :data:`twb.FORMAT_CHANGE_FLAGS` (Tableau writes them alphabetically).
DZV_FORMAT_FLAGS: tuple[str, ...] = (
    "DatagraphCoreV1",
    "DatagraphNodeDashboardZoneVisibilityV1",
    "DatagraphNodeSingleValueFieldV1",
    "ZoneVisibilityControl",
)


@dataclass(frozen=True)
class ZoneVisibility:
    """One zone whose visibility a boolean field controls.

    Attributes:
        zone_id: The dashboard zone's id (the layout engine's sequential number).
        field: The qualified boolean field (``[federated.x].[Show Detail]``).
    """

    zone_id: str
    field: str


def _guid(*parts: str) -> str:
    """Return a deterministic lower-case UUID for a datagraph slot.

    Args:
        *parts: What the guid identifies (the slot name, the zone, the field).

    Returns:
        A UUID5 string. Deterministic so a rebuild stays byte-identical; the graph only
        needs its guids to agree with each other, not to be random.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "twb:dzv:" + ":".join(parts)))


def render_datagraph(
    parent: ET.Element, visibilities: list[ZoneVisibility], dashboard_identifier: str
) -> None:
    """Render the ``<datagraph>`` that drives Dynamic Zone Visibility.

    Each controlled zone is two nodes and one edge: a ``single-value-field-node`` reads the
    boolean field, and its value output feeds a ``dashboard-zone-visibility-node``'s
    visibility input. Every node also has to be listed in ``<node-execution-subgraphs>``
    against the one execution subgraph, or Tableau evaluates none of them.

    Args:
        parent: The ``<workbook>`` element (the datagraph goes after ``<windows>``).
        visibilities: The controlled zones (nothing is emitted for an empty list).
        dashboard_identifier: The dashboard's ``<simple-id>`` uuid, braced and upper-case.
    """
    if not visibilities:
        return
    subgraph = _guid("subgraph")
    graph = ET.SubElement(ET.SubElement(parent, "datagraph"), "graph")
    ET.SubElement(
        ET.SubElement(graph, "properties"), "default-execution-subgraph-guid",
        {"value": subgraph},
    )

    # {zone id: (field node guid, value output guid, zone node guid, visibility input guid)}
    slots = {
        entry.zone_id: (
            _guid("field", entry.zone_id), _guid("value", entry.zone_id),
            _guid("zone", entry.zone_id), _guid("visibility", entry.zone_id),
        )
        for entry in visibilities
    }

    pairs = ET.SubElement(graph, "node-execution-subgraphs")
    for entry in visibilities:
        field_node, _, zone_node, _ = slots[entry.zone_id]
        for node in (field_node, zone_node):
            ET.SubElement(pairs, "pair", {
                "execution-subgraph-guid": subgraph, "node-guid": node,
            })

    nodes = ET.SubElement(graph, "nodes")
    for entry in visibilities:
        field_node, value_output, zone_node, visibility_input = slots[entry.zone_id]
        ET.SubElement(nodes, "single-value-field-node", {
            "fieldname": entry.field,
            "fieldname-input-guid": _guid("fieldname-input", entry.zone_id),
            "node-guid": field_node,
            "value-output-guid": value_output,
        })
        ET.SubElement(nodes, "dashboard-zone-visibility-node", {
            "dashboard-identifier": dashboard_identifier,
            "node-guid": zone_node,
            "visibility-input-guid": visibility_input,
            "zone-id": entry.zone_id,
        })

    edges = ET.SubElement(graph, "edges")
    for entry in visibilities:
        _, value_output, _, visibility_input = slots[entry.zone_id]
        ET.SubElement(edges, "edge", {"from": value_output, "to": visibility_input})
    ET.SubElement(graph, "pin-values")
