"""Worksheet bodies for the tableau-build assembler (CONTRACT.md step 8).

:mod:`twb` owns the workbook shell - datasources, dashboard, windows. This module owns what
goes *inside* a ``<worksheet>``: the mark class, the shelves, the encodings, the sorts, the
filters and the design-token styling. Every one of the 15 validated legacy patterns (bar,
sorted / filtered / styled bar, stacked bar, line, area, pie, scatter, text table, KPI card,
histogram, map, dual axis, combo, custom tooltip) is produced from manifest fields here -
nothing is copy-pasted from a snippet, so no snippet's field names, datasource ids or UUIDs
can leak into an analyst's workbook.

The 15 patterns are **not** 15 chart types. Four of them (sorted, filtered, styled, custom
tooltip) are modifiers that apply to *any* chart, and "stacked bar" is a bar with a colour
encoding. So the shape here is a :data:`CHART_SPECS` table of what a chart type changes
(mark class, pane count, whether the marks carry labels) crossed with modifier renderers that
each read one optional manifest key. A new chart type is a row in the table, not a function.

Everything is pure and stdlib-only: text in, XML elements out, no filesystem.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, fields
from typing import Callable, NamedTuple, Optional

# A worksheet that reads a parameter has to declare it; :mod:`features` owns what a parameter
# column looks like and imports nothing back, so no cycle.
from features import PARAMETERS_DATASOURCE, Parameter, render_parameter_column

# --- CDATA -------------------------------------------------------------------
# ponytail: ElementTree cannot emit CDATA, and Tableau *requires* it around field
# references in <run> elements (entity-encoded refs render as literal text, which the
# semantic validator rejects). A sentinel pair plus one regex at serialisation time is far
# cheaper than a custom serializer.

#: Private-use characters wrapped around text that must serialise as a CDATA section.
#: Written as escapes, not literals: the source has to survive a cp1252 console.
CDATA_OPEN = "\ue000"
CDATA_CLOSE = "\ue001"

_CDATA_SPAN = re.compile(f"{CDATA_OPEN}(.*?){CDATA_CLOSE}", re.DOTALL)
_ENTITIES = (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'"), ("&amp;", "&"))


def unwrap_cdata(xml_text: str) -> str:
    """Turn every sentinel-marked span in serialised XML into a real CDATA section.

    Args:
        xml_text: The output of ``ET.tostring``, still carrying the sentinels.

    Returns:
        The same XML with each span rewritten as ``<![CDATA[...]]>`` and the escaping
        ElementTree applied inside it undone.
    """
    def _restore(match: re.Match) -> str:
        inner = match.group(1)
        for entity, character in _ENTITIES:  # &amp; last: it is the escape of the escapes
            inner = inner.replace(entity, character)
        return f"<![CDATA[{inner}]]>"

    return _CDATA_SPAN.sub(_restore, xml_text)


def cdata(text: str) -> str:
    """Wrap ``text`` so :func:`unwrap_cdata` emits it as a CDATA section."""
    return f"{CDATA_OPEN}{text}{CDATA_CLOSE}"


# --- Design tokens ------------------------------------------------------------

#: Tableau's own defaults, used for any token DESIGN-TOKENS.md does not carry (and for every
#: token when the file is absent) - "neutral" means Tableau's look, not an invented one.
DEFAULT_FONT = "Tableau Book"
DEFAULT_TITLE_SIZE = 12
DEFAULT_TITLE_COLOR = "#000000"
DEFAULT_KPI_SIZE = 22

_HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_SIZE = re.compile(r"(\d+)\s*px", re.IGNORECASE)


#: The name the worksheet's inline brand palette is declared and referenced under.
BRAND_PALETTE_NAME = "Brand"

_SERIES_HEADING = re.compile(r"^#+\s*.*series colors", re.IGNORECASE)


@dataclass(frozen=True)
class DesignTokens:
    """The slice of DESIGN-TOKENS.md a worksheet body can actually apply.

    Tableau styles a *worksheet* with a font family, a title run, cell/axis formats, and the
    palette its marks are coloured from.

    **How the palette gets its colours without knowing the data's members.** A palette that
    binds hex values to concrete members (``<map to='#...'><bucket>"West"</bucket>``) is
    unbuildable from a manifest - the builder never sees the data. It does not have to be: an
    ``<encoding>`` may carry an inline ``<color-palette>``, which lists the colours *in order*
    and leaves Tableau to walk the field's domain against them, exactly as it does with its own
    default 10. So the brand's ordered series colours *are* the palette, member values are
    never needed, and a coloured chart is on-brand instead of on Tableau-default.

    Attributes:
        font_family: Body font for the whole worksheet.
        title_size: Chart-title point size.
        title_color: Chart-title colour, lower-cased hex.
        kpi_size: Point size for a KPI card's big number.
        series_colors: The brand's ordered chart-series colours, lower-cased hex.
        present: Whether a DESIGN-TOKENS.md was actually supplied.
    """

    font_family: str = DEFAULT_FONT
    title_size: int = DEFAULT_TITLE_SIZE
    title_color: str = DEFAULT_TITLE_COLOR
    kpi_size: int = DEFAULT_KPI_SIZE
    series_colors: tuple[str, ...] = ()
    present: bool = False


def parse_design_tokens(tokens_text: str) -> DesignTokens:
    """Read the typography and palette tokens out of a DESIGN-TOKENS.md.

    The parse is tolerant by design (the file is prose an agent authored from a template):
    it looks for the ``- **Font family**:`` and ``- **Chart title**:`` bullets and takes the
    first font name / px size / hex colour on each, and reads every hex under the
    ``### Chart series colors`` heading as the ordered palette. Anything it cannot find keeps
    its Tableau default.

    Args:
        tokens_text: The contents of a ``DESIGN-TOKENS.md`` (``""`` when absent).

    Returns:
        The parsed :class:`DesignTokens`; ``present`` is False for empty input.
    """
    if not tokens_text.strip():
        return DesignTokens()

    font, title_size, title_color = DEFAULT_FONT, DEFAULT_TITLE_SIZE, DEFAULT_TITLE_COLOR
    series_colors: list[str] = []
    in_series = False
    for raw_line in tokens_text.splitlines():
        line = raw_line.strip()
        if line.startswith("#"):
            # The palette is a *section*, not a bullet: the template asks for an ordered list
            # and every author formats that differently (one comma-separated run, a bullet
            # each), so every hex until the next heading is a series colour.
            in_series = _SERIES_HEADING.match(line) is not None
            continue
        if in_series:
            series_colors += [match.group(0).lower() for match in _HEX.finditer(line)]
            continue
        if not line.startswith("-"):
            continue
        label, _, value = line.lstrip("- ").partition(":")
        label = label.strip().strip("*").lower()
        value = value.strip()
        if not value or value.startswith("["):  # an unfilled template placeholder
            continue

        if label == "font family":
            font = value.strip("`*")
        elif label == "chart title":
            size = _SIZE.search(value)
            if size:
                title_size = int(size.group(1))
            color = _HEX.search(value)
            if color:
                title_color = color.group(0).lower()

    return DesignTokens(
        font_family=font,
        title_size=title_size,
        title_color=title_color,
        kpi_size=max(title_size + 8, DEFAULT_KPI_SIZE),
        series_colors=tuple(series_colors),
        present=True,
    )


# --- Field references ----------------------------------------------------------
# A shelf/encoding entry names a field; Tableau needs a <column>, a <column-instance>, and a
# qualified reference for it, all three consistent. These tables are the single place the
# naming rules live.

#: manifest aggregation -> (column-instance prefix, ``derivation`` attribute).
AGGREGATION_DERIVATIONS: dict[str, tuple[str, str]] = {
    "sum": ("sum", "Sum"),
    "avg": ("avg", "Avg"),
    "min": ("min", "Min"),
    "max": ("max", "Max"),
    "count": ("cnt", "Count"),
    "countd": ("ctd", "CountD"),
    "median": ("med", "Median"),
    "attr": ("attr", "Attribute"),
    "none": ("none", "None"),
}

#: Aggregations that turn any field into a continuous measure on the shelf.
_AGGREGATING = frozenset({"sum", "avg", "min", "max", "count", "countd", "median"})

#: A calculated field that already aggregates cannot be aggregated again - ``SUM()`` of
#: ``SUM([profit]) / SUM([revenue])`` is an error Tableau refuses at load. Such a field goes
#: on a shelf as-is, which Tableau records as the ``User`` derivation.
USER_DERIVATION = ("usr", "User")

_AGGREGATE_CALL = re.compile(
    r"\b(SUM|AVG|MIN|MAX|COUNT|COUNTD|MEDIAN|ATTR|STDEV|STDEVP|VAR|VARP|PERCENTILE"
    r"|CORR|COVAR|COVARP|TOTAL|WINDOW_\w+|RUNNING_\w+)\s*\(",
    re.IGNORECASE,
)


def strip_lod_expressions(formula: str) -> str:
    """Return the formula with every brace-delimited LOD expression removed.

    Args:
        formula: The calculation's Tableau formula.

    Returns:
        The formula with each ``{...}`` span (nesting included) replaced by a space, so what
        is left is only what the formula computes *around* its LODs. A formula whose braces
        do not balance has no LOD to strip and is returned unchanged.

    The balance check is what keeps an unclosed brace from swallowing the rest of the
    formula: ``"{" + STR([Ratio])`` is a string literal, not an LOD, and stripping from the
    ``{`` onwards hid the ``[Ratio]`` reference from
    :func:`aggregate_calculated_fields` - which put the field back on the shelf as ``none:``,
    the very bug issue #62 fixes.
    """
    if formula.count("{") != formula.count("}"):
        return formula
    kept: list[str] = []
    depth = 0
    for character in formula:
        if character == "{":
            depth += 1
            kept.append(" ")
        elif character == "}":
            depth = max(0, depth - 1)
        elif depth == 0:
            kept.append(character)
    return "".join(kept)


def is_aggregate_formula(formula: str) -> bool:
    """Return whether a calculated field's formula already aggregates.

    Args:
        formula: The calculation's Tableau formula.

    Returns:
        True when the formula calls an aggregate function *outside* any LOD expression, so the
        field must reach a shelf un-aggregated. A row-level formula
        (``[quantity] * [unit_price]``) returns False and is treated like any other measure.

    An **LOD expression** (``{FIXED [region]: SUM([revenue])}``) returns False even though it
    contains ``SUM(``: an LOD produces one row-level value per its own grain, so Tableau
    aggregates it again on the shelf (``SUM([Regional Revenue])``). Treating it as
    pre-aggregated is what put the ``User`` derivation on it and made the shelf read one
    arbitrary member's value. Only the aggregate *inside* the braces is discounted, though -
    ``SUM([revenue]) / {FIXED : SUM([revenue])}`` aggregates and must not be re-aggregated,
    which is the whole point of a percent-of-total calc.
    """
    if not formula:
        return False
    return _AGGREGATE_CALL.search(strip_lod_expressions(formula)) is not None


#: A bracketed field reference inside a formula (``[ACV - Current]``). Field names cannot
#: contain brackets, so the non-greedy character class is the whole grammar.
_FIELD_REFERENCE = re.compile(r"\[([^\[\]]+)\]")


def aggregate_calculated_fields(calculated: dict[str, CalculatedField]) -> frozenset[str]:
    """Return the names of the calculated fields that aggregate, transitively.

    Args:
        calculated: ``{name: CalculatedField}`` for one datasource.

    Returns:
        Every name whose formula aggregates directly (:func:`is_aggregate_formula`) *or*
        references another aggregate calculated field.

    Tableau's rule is transitive: ``[Avg Sale Size] = [ACV] / [Sales]`` calls no aggregate
    function of its own, but both operands are aggregates, so the result is one too and has
    no row-level value to put on a shelf. Issue #62: detecting only the direct call found 5
    of the 16 aggregates in the reporting workbook, and the other 11 reached Desktop as
    ``sum:`` (numeric, double aggregation) or ``none:`` (string) - red pills either way.

    LOD expressions are stripped before the reference scan for the same reason
    :func:`is_aggregate_formula` strips them: ``{FIXED [region]: SUM([ACV])}`` is row-level
    at its own grain, so referencing an aggregate from *inside* the braces does not make the
    referring field an aggregate.
    """
    aggregate = {
        name for name, calculation in calculated.items()
        if is_aggregate_formula(calculation.formula)
    }
    # Fixpoint rather than one pass: a chain (YoY Change -> YoY Label -> YoY Direction) only
    # resolves fully once each hop has joined the set. Bounded by len(calculated).
    growing = True
    while growing:
        growing = False
        for name, calculation in calculated.items():
            if name in aggregate:
                continue
            referenced = set(
                _FIELD_REFERENCE.findall(strip_lod_expressions(calculation.formula))
            )
            if referenced & aggregate:
                aggregate.add(name)
                growing = True
    return frozenset(aggregate)


#: manifest ``table_calc`` -> the column-instance name's extra prefix. The keys are the
#: XSD's ``TCType-ST`` enumeration (minus ``None``, which is "no table calc"); the prefixes
#: are cosmetic identifiers - ``cum:sum:revenue:qk`` is what Tableau writes for a running
#: total, and ``manifest.TABLE_CALCS`` reads this table so an unknown type fails validation
#: rather than rendering a calc Tableau does not have.
#: ponytail: only ``PctTotal`` -> ``pcto`` is attested (Desktop 2025.1 renamed ours on save).
#: The rest are inferred; a wrong one costs nothing but a rewrite on open, and is fixed by
#: reading the name out of a Desktop-saved workbook that uses that calc - issue #50 carries
#: the exact sheet-by-sheet steps.
TABLE_CALC_PREFIXES: dict[str, str] = {
    "CumTotal": "cum",
    "WindowTotal": "wnd",
    "Difference": "diff",
    "PctDiff": "pctdiff",
    "PctValue": "pctval",
    "PctTotal": "pcto",
    "Rank": "rank",
    "PctRank": "pctrank",
}

#: How a table calc walks the view. ``Rows`` is Tableau's default "Table (across)" addressing
#: - the one a running total or percent-of-total means on a normal chart.
TABLE_CALC_ORDERING = "Rows"


#: manifest date_part -> (prefix, derivation). ``date`` is the exact date: no truncation,
#: but still continuous, which is what makes a line/area chart draw a line. Only the levels
#: WORKSHEETS.md documents from real Tableau output are here - ``manifest.DATE_PARTS`` reads
#: this table, so an hour/minute request fails validation rather than guessing a prefix.
DATE_PART_DERIVATIONS: dict[str, tuple[str, str]] = {
    "year": ("tyr", "Year-Trunc"),
    "quarter": ("tqr", "Quarter-Trunc"),
    "month": ("tmn", "Month-Trunc"),
    "week": ("twk", "Week-Trunc"),
    "day": ("tdy", "Day-Trunc"),
    "date": ("none", "None"),
}

#: The column-instance name's trailing key, per instance type.
_TYPE_SUFFIX = {"quantitative": "qk", "nominal": "nk", "ordinal": "ok"}

#: Tableau's built-in pivot fields, referenced qualified but never declared.
MEASURE_NAMES = "[:Measure Names]"

#: The basemap a map worksheet draws on. Declared at the workbook level *and* in the view.
MAPSOURCE_NAME = "Tableau"

#: Tableau's generated geographic fields - a map's shelves and geometry encoding.
GENERATED_LONGITUDE = "[Longitude (generated)]"
GENERATED_LATITUDE = "[Latitude (generated)]"
GENERATED_GEOMETRY = "[Geometry (generated)]"

#: What a binned column looks like: always an integer dimension, sliced ordinally.
BIN_DATATYPE = "integer"
#: Decimal places Tableau records on a bin calculation.
BIN_DECIMALS = "2"


def caption_for(field_name: str) -> str:
    """Return the UI caption for a field name (``order_date`` -> ``Order Date``).

    Title-casing is for raw snake_case columns only. A name the author already cased -
    ``ACV - Current``, ``YoY Direction``, ``In KPI Window`` - is passed through verbatim:
    ``.title()`` would lower-case the rest of every acronym (issue #69).
    """
    spaced_name = field_name.replace("_", " ")
    return spaced_name.title() if spaced_name == spaced_name.lower() else spaced_name


class CalculatedField(NamedTuple):
    """One declared calculated field, as the resolver needs it.

    Attributes:
        formula: The Tableau formula.
        datatype: The result's type (``real`` when the manifest declares none).
        number_format: A ``default-format`` pattern for the column, or ``""``. Set on the
            *column* rather than as a worksheet style rule, so every sheet that uses the
            field - and the data pane - shows it the same way.
    """

    formula: str
    datatype: str
    number_format: str = ""


@dataclass(frozen=True)
class FieldRef:
    """One resolved shelf/encoding entry: the column, its instance, and how to name both.

    Attributes:
        field_name: The bare field name from the manifest.
        datatype: The Tableau datatype of the underlying column.
        role: ``dimension`` / ``measure``.
        column_type: The ``type`` attribute of the ``<column>``.
        instance_type: The ``type`` attribute of the ``<column-instance>``.
        prefix: The instance-name prefix (``sum``, ``tmn``, ``none``, ...).
        derivation: The instance's ``derivation`` attribute.
        formula: A calculated field's formula, else ``""``.
        number_format: A calculated field's ``default-format`` pattern, else ``""``.
        bin_size: Bin width when the entry asked for a binned column, else ``None``.
        bin_source: The measure a bin is computed from, else ``""``.
        table_calc: A :data:`TABLE_CALC_PREFIXES` key when the entry asked for a table
            calculation, else ``""``. A table calc lives on the *instance*, not on a column
            of its own - the same measure can be plain on one shelf and cumulative on another.
    """

    field_name: str
    datatype: str
    role: str
    column_type: str
    instance_type: str
    prefix: str
    derivation: str
    formula: str = ""
    number_format: str = ""
    bin_size: Optional[float] = None
    bin_source: str = ""
    table_calc: str = ""

    @property
    def column_name(self) -> str:
        """str: The bracketed column name (``[revenue]``, ``[Revenue (bin)]``)."""
        return f"[{self.field_name}]"

    @property
    def instance_name(self) -> str:
        """str: The bracketed column-instance name (``[sum:revenue:qk]``, ``[cum:sum:...]``)."""
        prefix = self.prefix
        if self.table_calc:
            prefix = f"{TABLE_CALC_PREFIXES[self.table_calc]}:{prefix}"
        return f"[{prefix}:{self.field_name}:{_TYPE_SUFFIX[self.instance_type]}]"

    @property
    def caption(self) -> str:
        """str: The caption Tableau shows for the column.

        A bin's name is already a caption (``Revenue (bin)``) and ``caption_for`` passes
        cased names through, so the marker stays ``(bin)`` and keeps matching the column.
        """
        return caption_for(self.field_name)


class FieldResolver:
    """Turns manifest shelf/encoding entries into :class:`FieldRef`s for one datasource.

    Args:
        datasource_id: The ``federated.*`` id every reference is qualified with.
        datasource_caption: The datasource's manifest name, as the view declares it.
        field_types: ``{field name: DATA-MODEL.md type}`` for the datasource's CSV.
        calculated: ``{name: CalculatedField}`` for the datasource's calculated fields.
        type_facts: ``{type: (role, column type)}`` - :mod:`twb`'s type table, passed in so
            this module stays free of a circular import.
    """

    def __init__(
        self,
        datasource_id: str,
        datasource_caption: str,
        field_types: dict[str, str],
        calculated: dict[str, CalculatedField],
        type_facts: dict[str, tuple[str, str]],
    ) -> None:
        self.datasource_id = datasource_id
        self.datasource_caption = datasource_caption
        self.field_types = field_types
        self.calculated = calculated
        self.type_facts = type_facts
        # Computed once here rather than per reference: the closure is over the whole
        # datasource, and one sheet resolves the same field on several shelves.
        self.aggregate_calcs = aggregate_calculated_fields(calculated)

    def qualify(self, name: str) -> str:
        """Return a bracketed name qualified with the datasource (``[ds].[name]``)."""
        return f"[{self.datasource_id}].{name}"

    def is_aggregate(self, field_name: str) -> bool:
        """Return whether the named field is a calculated field that aggregates.

        Args:
            field_name: The bare field name from the manifest.

        Returns:
            True when the field's own formula aggregates or it references a calculated
            field that does - either way it reaches a shelf un-aggregated.
        """
        return field_name in self.aggregate_calcs

    def reference(self, entry: object) -> Optional[FieldRef]:
        """Resolve one shelf/encoding entry.

        Args:
            entry: A bare field name, or an object with ``field`` plus any of
                ``aggregation`` / ``date_part`` / ``bin``.

        Returns:
            The resolved :class:`FieldRef`, or ``None`` when the entry names no field
            (``manifest.validate_manifest`` has already rejected that case).
        """
        table_calc = ""
        if isinstance(entry, dict):
            field_name = str(entry.get("field", "")).strip().strip("[]").strip()
            aggregation = _lower(entry.get("aggregation"))
            date_part = _lower(entry.get("date_part"))
            raw_bin = entry.get("bin")
            bin_size = (
                raw_bin
                if isinstance(raw_bin, (int, float)) and not isinstance(raw_bin, bool)
                else None
            )
            raw_calc = entry.get("table_calc")
            if isinstance(raw_calc, str) and raw_calc.strip() in TABLE_CALC_PREFIXES:
                table_calc = raw_calc.strip()
        else:
            field_name = str(entry or "").strip().strip("[]").strip()
            aggregation, date_part, bin_size = "", "", None
        if not field_name:
            return None

        formula, declared_type, number_format = self.calculated.get(
            field_name, CalculatedField("", "")
        )
        datatype = declared_type or self.field_types.get(field_name, "string")
        role, column_type = self.type_facts.get(datatype, ("dimension", "nominal"))

        if bin_size is not None:
            # A bin is a *new* dimension column computed from the measure, not a derivation
            # of it - hence its own name, datatype and ordinal slicing.
            return FieldRef(
                field_name=f"{caption_for(field_name)} (bin)",
                datatype=BIN_DATATYPE,
                role="dimension",
                column_type="ordinal",
                instance_type="ordinal",
                prefix="none",
                derivation="None",
                bin_size=float(bin_size),
                bin_source=field_name,
            )

        # An unknown aggregation / date part cannot reach here from a validated manifest;
        # falling back to the un-derived instance keeps a direct call from crashing.
        if date_part and datatype in {"date", "datetime"}:
            prefix, derivation = DATE_PART_DERIVATIONS.get(date_part, ("none", "None"))
            instance_type = "quantitative"
        elif self.is_aggregate(field_name):
            # Issue #62: this branch sits *above* the aggregation one on purpose. An
            # aggregate calculated field has no row-level value, so every instance but
            # 'usr:' is a pill Desktop refuses ("can't be applied to a user-defined
            # aggregate"). Whatever the entry's 'aggregation' key says, the answer is the
            # same: BUILD-MANIFEST-TEMPLATE.md's documented "none" means "do not
            # re-aggregate", which *is* the User derivation, and any real aggregation
            # ("sum" on a ratio) is rejected by manifest validation before reaching here.
            prefix, derivation = USER_DERIVATION
            instance_type = column_type
        elif aggregation:
            prefix, derivation = AGGREGATION_DERIVATIONS.get(aggregation, ("none", "None"))
            if aggregation in _AGGREGATING:
                instance_type = "quantitative"
            elif aggregation == "none" and role == "measure":
                # Issue #59: an explicit "none" on a plain measure is the analyst asking for a
                # discrete pill, not a number. Left at the measure's own 'quantitative' it is
                # continuous, and a continuous pill on Rows draws an axis - the demo's customer
                # table grew one 90/80/70 Nps Score axis per customer. 'ordinal' is what
                # Desktop writes for a measure set to Discrete with no aggregation.
                #
                # Only a *plain* measure reaches here; an aggregate calc took the branch above,
                # where "none" keeps it one continuous number (making it discrete broke the
                # demo's AOV KPI card).
                instance_type = "ordinal"
            else:
                instance_type = column_type
        elif role == "measure":
            # A measure on a shelf without an explicit aggregation is SUM - Tableau's own
            # default, and what every spec row means by "revenue on Rows".
            prefix, derivation, instance_type = "sum", "Sum", "quantitative"
        else:
            prefix, derivation, instance_type = "none", "None", column_type

        return FieldRef(
            field_name=field_name,
            datatype=datatype,
            role=role,
            column_type=column_type,
            instance_type=instance_type,
            prefix=prefix,
            derivation=derivation,
            formula=formula,
            number_format=number_format,
            table_calc=table_calc,
        )

    def references(self, entries: object) -> list[FieldRef]:
        """Resolve a shelf's list of entries, dropping any that name no field."""
        if entries is None:
            return []
        raw = entries if isinstance(entries, list) else [entries]
        return [ref for ref in (self.reference(entry) for entry in raw) if ref is not None]


def _lower(value: object) -> str:
    """Lower-case a manifest string value; anything else (``null``) reads as absent."""
    return value.strip().lower() if isinstance(value, str) else ""


# --- Chart types ---------------------------------------------------------------

@dataclass(frozen=True)
class ChartSpec:
    """What a chart type changes about an otherwise identical worksheet body.

    Attributes:
        mark_class: The ``<mark class='...'>`` for the single pane.
        label_marks: Show mark labels (a text table's numbers, a pie's slice values).
        kpi_card: Centre the cell text and render the text encoding as one big
            number - the KPI card treatment.
        dual: Two measures share one axis pair: 3 panes and an axis-sync style rule.
        pane_marks: Per-pane mark classes for a dual chart (a combo's Bar then Line);
            empty means every pane keeps ``mark_class``.
        geographic: Shelves are Tableau's generated lat/long and the marks carry geometry.
        empty_shelves: The chart puts nothing on Rows/Cols (pie, KPI card).
    """

    mark_class: str = "Automatic"
    label_marks: bool = False
    kpi_card: bool = False
    dual: bool = False
    pane_marks: tuple[str, ...] = ()
    geographic: bool = False
    empty_shelves: bool = False


#: Every chart type the builder emits, and what it changes. The four legacy "chart types"
#: that are really modifiers - sorted, filtered, styled, custom tooltip - are the optional
#: ``sort`` / ``filters`` / design tokens / ``tooltip`` manifest keys, applicable to any row
#: here; a stacked bar is ``bar`` with a ``color`` encoding.
#:
#: Issue #64: every single-pane type names its mark explicitly. ``Automatic`` picks by
#: shelf shape - a *discrete* dimension x measure draws bars, but a *continuous* date x
#: measure draws points - so a ``bar`` over the ``date_part`` month spine that
#: BUILD-MANIFEST-TEMPLATE.md steers date axes to rendered as scattered dots.
#: ``chart_type`` is the analyst's stated intent; nothing is left to Tableau's inference.
CHART_SPECS: dict[str, ChartSpec] = {
    "bar": ChartSpec(mark_class="Bar"),
    "line": ChartSpec(mark_class="Line"),
    "area": ChartSpec(mark_class="Area"),
    "pie": ChartSpec(mark_class="Pie", label_marks=True, empty_shelves=True),
    "scatter": ChartSpec(mark_class="Circle"),
    "map": ChartSpec(geographic=True),
    "text": ChartSpec(mark_class="Text", label_marks=True, kpi_card=True,
                      empty_shelves=True),
    "table": ChartSpec(mark_class="Text", label_marks=True),
    "heatmap": ChartSpec(mark_class="Square"),
    "histogram": ChartSpec(mark_class="Bar"),
    "treemap": ChartSpec(mark_class="Square", label_marks=True),
    "bullet": ChartSpec(mark_class="Bar"),
    "gantt": ChartSpec(mark_class="Gantt"),
    "boxplot": ChartSpec(mark_class="Circle"),
    "dual-axis": ChartSpec(dual=True),
    "combo": ChartSpec(dual=True, pane_marks=("Bar", "Line")),
}

#: Encoding names the builder emits, in the order Tableau writes them. An encoding the
#: manifest names but this list does not is ignored rather than emitted blind.
ENCODING_ORDER: tuple[str, ...] = (
    "color", "size", "shape", "text", "lod", "wedge-size", "geometry", "tooltip",
)

#: Encodings that need a legend card on the window's right edge.
LEGEND_ENCODINGS: tuple[str, ...] = ("color", "size", "shape")

#: The pane attribute every snippet carries.
PANE_RELAXATION = {"selection-relaxation-option": "selection-relaxation-allow"}

#: manifest ``fit`` -> the ``<zoom>`` type the sheet's viewpoint carries (the XSD's
#: ``VisualDoc-ZoomType-ST``). ``standard`` is Tableau's un-zoomed default and writes no
#: ``<zoom>`` at all - it is the *absence* of a fit, not a value of one.
FIT_ZOOMS: dict[str, str] = {
    "standard": "",
    "entire-view": "entire-view",
    "fit-width": "fit-width",
    "fit-height": "fit-height",
}

#: What a sheet fits to when the manifest says nothing. Entire View, because a dashboard zone
#: is a fixed box: Standard leaves the chart at its natural size, floating in the zone's
#: whitespace, which is what made every generated sheet look unfinished.
DEFAULT_FIT = "entire-view"

#: Chart types that keep Standard fit by default - a text table is meant to *scroll*, and
#: squeezing 200 rows into a zone renders it as unreadable slivers.
STANDARD_FIT_CHART_TYPES = frozenset({"table"})

#: Tableau's line break inside a formatted-text run: AE ligature + tab. Not ``\n``.
TOOLTIP_BREAK = "\u00c6\t"


# --- Worksheet parts ------------------------------------------------------------

@dataclass(frozen=True)
class FilterPlan:
    """One resolved filter: the field, the members or bounds, and whether it is a context
    filter (which runs before FIXED LODs and every other filter).

    Attributes:
        reference: The filtered field.
        members: Explicit members for a categorical filter; empty for a range filter.
        minimum: Lower bound of a range filter, or ``None``.
        maximum: Upper bound of a range filter, or ``None``.
        context: Whether Tableau evaluates it first.
        all_members: Filter *on* the field with nothing excluded - the worksheet side of a
            quick-filter card, whose whole job is to let the viewer do the excluding.
    """

    reference: FieldRef
    members: tuple[str, ...] = ()
    minimum: Optional[str] = None
    maximum: Optional[str] = None
    context: bool = False
    all_members: bool = False


@dataclass(frozen=True)
class SortPlan:
    """One resolved sort: computed (``by`` a measure) or manual (an explicit ``order``).

    Attributes:
        reference: The dimension being sorted.
        by: The measure that determines the order, for a computed sort.
        order: The explicit member order, for a manual sort.
        direction: ``ASC`` or ``DESC``.
    """

    reference: FieldRef
    by: Optional[FieldRef] = None
    order: tuple[str, ...] = ()
    direction: str = "DESC"


#: A reference line's ``formula`` - the aggregation of the field it draws at (XSD enum).
REFERENCE_LINE_FORMULAS: frozenset[str] = frozenset({
    "constant", "total", "sum", "min", "max", "average", "median", "quantiles",
    "percentile", "stdev", "confidence", "medianconfidence",
})

#: How far a reference line's computation reaches (XSD enum).
REFERENCE_LINE_SCOPES: frozenset[str] = frozenset({"per-cell", "per-pane", "per-table"})

#: The confidence probability Desktop writes on every reference line (only the ``confidence``
#: and ``percentile`` formulas read it; the rest carry it and ignore it).
REFERENCE_LINE_PROBABILITY = "95"

#: What a reference line labels itself with (XSD enum). ``custom`` is implied by a ``label``.
REFERENCE_LINE_LABEL_TYPES: frozenset[str] = frozenset({
    "none", "automatic", "value", "computation", "custom",
})


@dataclass(frozen=True)
class ReferenceLinePlan:
    """One resolved reference line: the measure it draws at, and how it computes and labels.

    Attributes:
        reference: The measure the line is drawn against (it is both the line's axis and its
            value - a reference line always lives on the axis of the field it summarises).
        formula: The aggregation drawn (``average``, ``median``, ...).
        scope: ``per-cell`` / ``per-pane`` / ``per-table``.
        label_type: What the line labels itself with; ``custom`` when :attr:`label` is set.
        label: A custom label template (``<Computation>: <Value>``), or ``""``.
    """

    reference: FieldRef
    formula: str = "average"
    scope: str = "per-table"
    label_type: str = "computation"
    label: str = ""


#: The horizontal / vertical alignments a ``format`` block accepts. ``manifest`` reads these
#: (and :data:`SHEET_FORMAT_KEYS`), so a typo'd key or value fails validation instead of
#: silently rendering an unformatted sheet.
TEXT_ALIGNMENTS: frozenset[str] = frozenset({"left", "center", "right"})
VERTICAL_ALIGNMENTS: frozenset[str] = frozenset({"top", "center", "bottom"})

#: The one non-colour value a shading / borders / lines key takes: "there is no such line".
#: Tableau hides a line by turning its display off, not by colouring it the background.
NO_FORMAT = "none"

#: What a bordered cell looks like when the format names a colour but no width - Desktop's own
#: hairline.
BORDER_STYLE = "solid"
BORDER_WIDTH = "1"


@dataclass(frozen=True)
class SheetFormat:
    """One worksheet's Format Borders / Lines / Shading / Alignment settings.

    These are the four Desktop format panes that make a workbook look designed rather than
    generated, and each is a handful of ``<style-rule>`` formats on the worksheet. A border or a
    line takes a hex or :data:`NO_FORMAT` (there is no such border); shading takes a hex only -
    a background cannot be shaded "none". The alignments are the same ``cell`` formats a KPI
    card centres itself with.

    Attributes:
        shading: The pane's background colour (Format > Shading).
        borders: The cell border colour, or :data:`NO_FORMAT` for a borderless sheet.
        gridlines: The gridline colour, or :data:`NO_FORMAT` to hide them.
        zero_lines: The zero-line colour, or :data:`NO_FORMAT` to hide it.
        align: Horizontal cell alignment (``left`` / ``center`` / ``right``).
        vertical_align: Vertical cell alignment (``top`` / ``center`` / ``bottom``).
    """

    shading: str = ""
    borders: str = ""
    gridlines: str = ""
    zero_lines: str = ""
    align: str = ""
    vertical_align: str = ""


#: What a ``format`` block may carry - derived from the dataclass, because
#: :func:`_plan_sheet_format` splats the surviving keys straight into ``SheetFormat(**values)``
#: and a hand-kept copy that drifts is a TypeError.
SHEET_FORMAT_KEYS: frozenset[str] = frozenset(field.name for field in fields(SheetFormat))


@dataclass
class WorksheetPlan:
    """Everything one worksheet needs, resolved once and rendered from twice.

    :func:`render_worksheet` writes the body; :func:`legend_cards` and :func:`bin_columns`
    read the same plan for the window's legends and the datasource's derived columns. Every
    field reference - including the ones behind filters, sorts and tooltips - is resolved
    here, because :attr:`all_refs` is what declares them in ``<datasource-dependencies>``,
    and a field Tableau finds referenced but not declared is a broken worksheet.

    Attributes:
        name: The Tableau sheet name.
        spec: The chart type's :class:`ChartSpec`.
        resolver: The datasource's :class:`FieldResolver`.
        columns: Resolved Cols shelf entries.
        rows: Resolved Rows shelf entries.
        encodings: ``{encoding name: FieldRef}``.
        filters: Resolved filters, in manifest order.
        sort: The resolved sort, if any.
        tooltip: ``(label, field)`` pairs for a custom tooltip template.
        number_formats: ``(field, format pattern)`` pairs for the cell style rule.
        axis_titles: ``{"rows"|"columns": title}`` overrides.
        fit: How the sheet fills its zone - a :data:`FIT_ZOOMS` key.
        sheet_format: The resolved Format Borders / Lines / Shading / Alignment block.
        geo_role: A map's geographic semantic role, or ``""``.
        reference_lines: Resolved reference lines, in manifest order.
        parameters: Parameters the worksheet's calculations read - the view must declare them
            or Tableau cannot resolve the calculation. Filled in by :mod:`twb`, which owns
            the manifest's parameter list.
        declared: Fields the worksheet must declare without placing them on the view - a
            parameter action's source field, which is read off the clicked mark. Filled in by
            :mod:`twb` when it resolves the actions.
        detail: Fields pinned to the Detail shelf that no encoding names - the boolean a
            zone's Dynamic Zone Visibility reads, which Tableau evaluates off the *view* of a
            sheet in the dashboard. Filled in by :mod:`twb` from the layout's ``visibility``
            keys. A DZV field is a single value (a parameter comparison), so it never splits
            the marks.
    """

    name: str
    spec: ChartSpec
    resolver: FieldResolver
    columns: list[FieldRef] = field(default_factory=list)
    rows: list[FieldRef] = field(default_factory=list)
    encodings: dict[str, FieldRef] = field(default_factory=dict)
    filters: list[FilterPlan] = field(default_factory=list)
    reference_lines: list[ReferenceLinePlan] = field(default_factory=list)
    parameters: list[Parameter] = field(default_factory=list)
    declared: list[FieldRef] = field(default_factory=list)
    detail: list[FieldRef] = field(default_factory=list)
    sort: Optional[SortPlan] = None
    tooltip: list[tuple[str, FieldRef]] = field(default_factory=list)
    number_formats: list[tuple[FieldRef, str]] = field(default_factory=list)
    axis_titles: dict[str, str] = field(default_factory=dict)
    fit: str = DEFAULT_FIT
    sheet_format: SheetFormat = field(default_factory=SheetFormat)
    geo_role: str = ""

    def qualify(self, name: str) -> str:
        """Return a bracketed name qualified with this worksheet's datasource."""
        return self.resolver.qualify(name)

    def reference_of(self, reference: FieldRef) -> str:
        """Return the qualified column-instance reference for a resolved field."""
        return self.resolver.qualify(reference.instance_name)

    @property
    def zoom_type(self) -> str:
        """str: The sheet's ``<zoom>`` type; ``""`` for Standard fit, which writes no zoom."""
        return FIT_ZOOMS.get(self.fit, FIT_ZOOMS[DEFAULT_FIT])

    @property
    def all_refs(self) -> list[FieldRef]:
        """list[FieldRef]: Every field the worksheet references, from any of its parts."""
        references = [*self.columns, *self.rows, *self.encodings.values()]
        references += [entry.reference for entry in self.filters]
        if self.sort is not None:
            references.append(self.sort.reference)
            if self.sort.by is not None:
                references.append(self.sort.by)
        references += [reference for _, reference in self.tooltip]
        references += [reference for reference, _ in self.number_formats]
        references += [line.reference for line in self.reference_lines]
        references += self.declared
        references += self.detail
        return references


def plan_worksheet(entry: dict, resolver: FieldResolver) -> WorksheetPlan:
    """Resolve one manifest worksheet into a :class:`WorksheetPlan`.

    Args:
        entry: One ``worksheets`` entry from the build manifest.
        resolver: The :class:`FieldResolver` for the worksheet's datasource.

    Returns:
        The plan; an unknown chart type falls back to the plain ``bar`` spec, which
        ``manifest.validate_manifest`` has already rejected before the assembler runs.
    """
    chart_type = str(entry.get("chart_type", "")).strip().lower()
    spec = CHART_SPECS.get(chart_type, ChartSpec())
    shelves = entry.get("shelves") if isinstance(entry.get("shelves"), dict) else {}
    raw_encodings = entry.get("encodings") if isinstance(entry.get("encodings"), dict) else {}
    axis_titles = entry.get("axis_titles") if isinstance(entry.get("axis_titles"), dict) else {}

    encodings: dict[str, FieldRef] = {}
    for name in ENCODING_ORDER:
        if name not in raw_encodings:
            continue
        reference = resolver.reference(raw_encodings[name])
        if reference is not None:
            encodings[name] = reference

    return WorksheetPlan(
        name=str(entry.get("name", "")).strip(),
        spec=spec,
        resolver=resolver,
        columns=resolver.references(shelves.get("columns")),
        rows=resolver.references(shelves.get("rows")),
        encodings=encodings,
        filters=_plan_filters(entry.get("filters"), resolver),
        reference_lines=_plan_reference_lines(entry.get("reference_lines"), resolver),
        sort=_plan_sort(entry.get("sort"), resolver),
        tooltip=_plan_tooltip(entry.get("tooltip"), resolver),
        number_formats=_plan_number_formats(entry.get("number_formats"), resolver),
        axis_titles={
            shelf: str(title).strip()
            for shelf, title in axis_titles.items()
            if isinstance(title, str) and title.strip()
        },
        fit=_plan_fit(entry.get("fit"), chart_type),
        sheet_format=_plan_sheet_format(entry.get("format")),
        geo_role=str(entry.get("geo_role", "")).strip() if spec.geographic else "",
    )


def _plan_fit(value: object, chart_type: str) -> str:
    """Resolve the ``fit`` key, defaulting per chart type.

    Args:
        value: The manifest's ``fit`` value, if any.
        chart_type: The worksheet's chart type, which decides the default.

    Returns:
        A :data:`FIT_ZOOMS` key. An unknown value cannot reach here from a validated manifest
        and falls back to the default rather than emitting a zoom Tableau has no case for.
    """
    requested = _lower(value)
    if requested in FIT_ZOOMS:
        return requested
    return "standard" if chart_type in STANDARD_FIT_CHART_TYPES else DEFAULT_FIT


def _plan_sheet_format(block: object) -> SheetFormat:
    """Resolve the ``format`` block into a :class:`SheetFormat`, ignoring unknown keys."""
    if not isinstance(block, dict):
        return SheetFormat()
    values = {
        key: _lower(value) for key, value in block.items() if key in SHEET_FORMAT_KEYS
    }
    return SheetFormat(**values)


def _plan_filters(entries: object, resolver: FieldResolver) -> list[FilterPlan]:
    """Resolve the ``filters`` list into :class:`FilterPlan`s, dropping empty entries."""
    if not isinstance(entries, list):
        return []
    plans: list[FilterPlan] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        values = entry.get("values")
        members = tuple(str(value) for value in values) if isinstance(values, list) else ()
        minimum, maximum = entry.get("min"), entry.get("max")
        if not members and minimum is None and maximum is None:
            continue  # nothing to filter on

        # A range filter needs the *continuous* instance of a date: the discrete one has no
        # min/max to compare against, and Tableau ignores the filter.
        request = dict(entry)
        if not members and not request.get("date_part"):
            request["date_part"] = "date"
        reference = resolver.reference(request)
        if reference is None:
            continue
        plans.append(FilterPlan(
            reference=reference,
            members=members,
            minimum=None if minimum is None else _bound(minimum, reference.datatype),
            maximum=None if maximum is None else _bound(maximum, reference.datatype),
            context=entry.get("context") is True,
        ))
    return plans


def _plan_reference_lines(
    entries: object, resolver: FieldResolver
) -> list[ReferenceLinePlan]:
    """Resolve the ``reference_lines`` list into :class:`ReferenceLinePlan`s."""
    if not isinstance(entries, list):
        return []
    lines: list[ReferenceLinePlan] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        reference = resolver.reference(entry)
        if reference is None:
            continue
        label = str(entry.get("label", "")).strip()
        lines.append(ReferenceLinePlan(
            reference=reference,
            formula=_lower(entry.get("formula")) or "average",
            scope=_lower(entry.get("scope")) or "per-table",
            label_type="custom" if label else (_lower(entry.get("label_type")) or "computation"),
            label=label,
        ))
    return lines


def _plan_sort(entry: object, resolver: FieldResolver) -> Optional[SortPlan]:
    """Resolve the ``sort`` object into a :class:`SortPlan`, or ``None`` when absent."""
    if not isinstance(entry, dict):
        return None
    reference = resolver.reference(entry)
    if reference is None:
        return None
    order = entry.get("order")
    by = resolver.reference(entry["by"]) if entry.get("by") is not None else None
    if by is None and not (isinstance(order, list) and order):
        return None  # a sort naming neither a measure nor an order sorts by nothing
    return SortPlan(
        reference=reference,
        by=by,
        order=tuple(str(member) for member in order) if isinstance(order, list) else (),
        direction=str(entry.get("direction", "DESC")).strip().upper() or "DESC",
    )


def _plan_tooltip(entries: object, resolver: FieldResolver) -> list[tuple[str, FieldRef]]:
    """Resolve the ``tooltip`` list into ``(label, field)`` pairs.

    A bare dimension is wrapped in ``ATTR()`` on the way in: the Tooltip shelf does not add
    to the view's level of detail, so Tableau needs one value per mark and refuses the
    un-aggregated pill ("The field Region can't be displayed in Tooltips because it can't be
    converted to a measure using ATTR()"). Desktop makes the same substitution when a
    dimension is dropped there, so the manifest never has to spell it out.
    """
    if not isinstance(entries, list):
        return []
    pairs: list[tuple[str, FieldRef]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        reference = resolver.reference(entry)
        if reference is not None and _needs_attr_on_tooltip(entry, reference):
            reference = resolver.reference({**entry, "aggregation": "attr"})
        if reference is not None:
            pairs.append((str(entry.get("label", reference.caption)).strip(), reference))
    return pairs


def _needs_attr_on_tooltip(entry: dict, reference: FieldRef) -> bool:
    """Whether a resolved tooltip entry has to be re-resolved as ``ATTR()``.

    Args:
        entry: The manifest tooltip entry.
        reference: What :meth:`FieldResolver.reference` made of it.

    Returns:
        True for an un-aggregated dimension the manifest asked for plainly. An explicit
        ``aggregation`` (including ``none``) is the author's choice and is left alone; a
        measure already defaults to ``SUM``, a date part and a bin carry their own
        derivations, and an aggregating calculation resolves to the ``User`` derivation.
    """
    return (
        reference.role == "dimension"
        and reference.derivation == "None"
        and reference.bin_size is None
        and not _lower(entry.get("aggregation"))
    )


def _plan_number_formats(
    entries: object, resolver: FieldResolver
) -> list[tuple[FieldRef, str]]:
    """Resolve the ``number_formats`` list into ``(field, format pattern)`` pairs."""
    if not isinstance(entries, list):
        return []
    formats: list[tuple[FieldRef, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        pattern = str(entry.get("format", "")).strip()
        reference = resolver.reference(entry)
        if reference is not None and pattern:
            formats.append((reference, pattern))
    return formats


def bin_columns(plan: WorksheetPlan) -> list[FieldRef]:
    """Return the binned columns a worksheet introduces (histogram support).

    A bin is a real column on the datasource, not a worksheet-local derivation, so
    :mod:`twb` collects these across worksheets and declares them once per datasource.
    """
    return [reference for reference in plan.all_refs if reference.bin_size is not None]


def legend_cards(plan: WorksheetPlan) -> list[tuple[str, str, str]]:
    """Return the ``(card type, qualified field, pane id)`` triples for the right edge.

    Without a legend card a colour- or size-encoded chart renders, but the analyst has no
    key for it - which reads as a broken dashboard. A dual-axis / combo chart is coloured by
    the built-in Measure Names rather than by anything the manifest named, and its legend
    belongs to the first *measure* pane, not the shared pane 0.
    """
    if plan.spec.dual:
        return [("color", plan.qualify(MEASURE_NAMES), "1")]
    return [
        (name, plan.qualify(plan.encodings[name].instance_name), "0")
        for name in LEGEND_ENCODINGS
        if name in plan.encodings
    ]


# --- Rendering ------------------------------------------------------------------

def _render_run(parent: ET.Element, text: str, tokens: DesignTokens, size: int,
                color: str = "", bold: bool = False) -> None:
    """Append one styled ``<run>`` to a formatted-text block."""
    attributes = {"fontname": tokens.font_family, "fontsize": str(size)}
    if bold:
        attributes["bold"] = "true"
    if color:
        attributes["fontcolor"] = color.lower()
    ET.SubElement(parent, "run", dict(sorted(attributes.items()))).text = text


def _render_title(parent: ET.Element, tokens: DesignTokens) -> None:
    """Render the worksheet's title styling from the design tokens.

    ``<Sheet Name>`` is Tableau's placeholder: the run styles the title without hard-coding
    the sheet's name into it.
    """
    layout_options = ET.SubElement(parent, "layout-options")
    title = ET.SubElement(layout_options, "title")
    formatted = ET.SubElement(title, "formatted-text")
    _render_run(formatted, "<Sheet Name>", tokens, tokens.title_size, tokens.title_color)


def _render_dependencies(parent: ET.Element, plan: WorksheetPlan) -> None:
    """Declare every column and column-instance the worksheet references.

    Both are emitted in name order so the same manifest always produces byte-identical XML;
    the schema accepts them in any order.
    """
    dependencies = ET.SubElement(
        parent, "datasource-dependencies", {"datasource": plan.resolver.datasource_id}
    )
    by_column: dict[str, FieldRef] = {}
    by_instance: dict[str, FieldRef] = {}
    for reference in plan.all_refs:
        by_column.setdefault(reference.column_name, reference)
        by_instance.setdefault(reference.instance_name, reference)

    # A map's geographic dimension must say which geography it is, or Tableau plots nothing.
    geo_column = (
        plan.encodings["lod"].column_name
        if plan.geo_role and "lod" in plan.encodings else ""
    )

    for name in sorted(by_column):
        render_column(
            dependencies, by_column[name], plan.geo_role if name == geo_column else ""
        )

    for name in sorted(by_instance):
        render_column_instance(dependencies, by_instance[name])


def render_column_instance(parent: ET.Element, reference: FieldRef) -> None:
    """Render one ``<column-instance>`` - how a column is derived onto a shelf.

    Used inside a worksheet's ``<datasource-dependencies>``, at the datasource level (where a
    table calc's instance must also be declared) and in a dashboard's own dependencies.

    Args:
        parent: The element to append the instance to.
        reference: The resolved field.
    """
    instance = ET.SubElement(parent, "column-instance", {
        "column": reference.column_name,
        "derivation": reference.derivation,
        "name": reference.instance_name,
        "pivot": "key",
        "type": reference.instance_type,
    })
    if reference.table_calc:
        # A table calc is a property of the instance: the same measure can be plain on one
        # shelf and a running total on another.
        # No 'aggregation': Desktop 2025.1 strips it on save (the aggregation is already the
        # instance's own 'derivation'), and a workbook it rewrites on open is one whose calc
        # may not be the calc that was asked for.
        ET.SubElement(instance, "table-calc", {
            "ordering-type": TABLE_CALC_ORDERING,
            "type": reference.table_calc,
        })


def render_column(parent: ET.Element, reference: FieldRef, semantic_role: str = "") -> None:
    """Render one ``<column>`` - the field definition, plus any calculation behind it.

    Used both inside a worksheet's ``<datasource-dependencies>`` and at the datasource
    level (where the calculated and binned columns must also be declared).

    Args:
        parent: The element to append the column to.
        reference: The resolved field.
        semantic_role: A geographic role (``[Country].[ISO3166_2]``), or ``""``.
    """
    attributes = {
        "caption": reference.caption,
        "datatype": reference.datatype,
        "name": reference.column_name,
        "role": reference.role,
        "type": reference.column_type,
    }
    if reference.number_format:
        # On the column, not as a worksheet style rule: a ratio formatted as a percentage
        # once is formatted that way on every sheet and in the data pane.
        attributes["default-format"] = reference.number_format
    if semantic_role:
        attributes["semantic-role"] = semantic_role
    column = ET.SubElement(parent, "column", dict(sorted(attributes.items())))
    _render_calculation(column, reference)


def _render_calculation(column: ET.Element, reference: FieldRef) -> None:
    """Append the ``<calculation>`` child a bin or a calculated field carries."""
    if reference.bin_size is not None:
        ET.SubElement(column, "calculation", {
            "class": "bin",
            "decimals": BIN_DECIMALS,
            "formula": f"[{reference.bin_source}]",
            "peg": "0",
            "size": _bin_size_text(reference.bin_size),
        })
    elif reference.formula:
        ET.SubElement(column, "calculation", {"class": "tableau", "formula": reference.formula})


def _bin_size_text(value: float) -> str:
    """Render a bin size the way Tableau writes it - no trailing ``.0`` (``500``, not ``500.0``)."""
    return str(int(value)) if float(value).is_integer() else str(value)


def _render_filters(parent: ET.Element, plan: WorksheetPlan) -> list[str]:
    """Render the worksheet's filters and return the columns ``<slices>`` must list.

    Two shapes, both from the legacy patterns: a categorical filter over an explicit member
    list, and a quantitative in-range filter (a date or numeric window).

    Args:
        parent: The ``<view>`` element.
        plan: The worksheet plan.

    Returns:
        The qualified column names that were filtered, in manifest order.
    """
    filtered: list[str] = []
    for entry in plan.filters:
        qualified = plan.reference_of(entry.reference)
        attributes = {"column": qualified}
        if entry.context:
            # A context filter runs before FIXED LODs and every other filter.
            attributes["context"] = "true"

        if entry.members or entry.all_members:
            categorical = ET.SubElement(
                parent, "filter",
                dict(sorted({"class": "categorical", **attributes}.items())),
            )
            if entry.all_members:
                # "Every member, including ones added later" - the filter a quick-filter card
                # controls. An enumerated member list would freeze today's domain into it.
                ET.SubElement(categorical, "groupfilter", {
                    "function": "level-members",
                    "level": entry.reference.instance_name,
                    "user:ui-enumeration": "all",
                    "user:ui-marker": "enumerate",
                })
            else:
                _render_categorical(
                    categorical, entry.reference.instance_name, list(entry.members)
                )
        else:
            range_filter = ET.SubElement(parent, "filter", dict(sorted({
                "class": "quantitative", "included-values": "in-range", **attributes,
            }.items())))
            for tag, bound in (("min", entry.minimum), ("max", entry.maximum)):
                if bound is not None:
                    ET.SubElement(range_filter, tag).text = bound
        filtered.append(qualified)
    return filtered


def _render_categorical(parent: ET.Element, level: str, members: list[str]) -> None:
    """Render a categorical filter's members, wrapping >1 of them in a union."""
    ui_attributes = {
        "user:ui-domain": "database",
        "user:ui-enumeration": "inclusive",
        "user:ui-marker": "enumerate",
    }
    if len(members) == 1:
        ET.SubElement(parent, "groupfilter", {
            "function": "member", "level": level, "member": f'"{members[0]}"', **ui_attributes,
        })
        return
    union = ET.SubElement(parent, "groupfilter", {"function": "union", **ui_attributes})
    for member in members:
        ET.SubElement(union, "groupfilter", {
            "function": "member", "level": level, "member": f'"{member}"',
        })


def _bound(value: object, datatype: str) -> str:
    """Render a filter bound: dates take Tableau's ``#...#`` literal delimiters."""
    text = str(value).strip()
    if datatype in {"date", "datetime"} and not text.startswith("#"):
        return f"#{text}#"
    return text


def _render_sort(parent: ET.Element, plan: WorksheetPlan) -> None:
    """Render the worksheet's sort: computed (by a measure) or manual (an explicit order)."""
    if plan.sort is None:
        return
    column = plan.reference_of(plan.sort.reference)

    if plan.sort.order:
        # 'manual-sort', not 'sort': the schema's sort group is computed-sort / manual-sort /
        # natural-sort / alphabetic-sort, and a bare <sort> is rejected outright.
        manual = ET.SubElement(
            parent, "manual-sort", {"column": column, "direction": plan.sort.direction}
        )
        dictionary = ET.SubElement(manual, "dictionary")
        for member in plan.sort.order:
            ET.SubElement(dictionary, "bucket").text = f'"{member}"'
        return

    ET.SubElement(parent, "computed-sort", {
        "column": column,
        "direction": plan.sort.direction,
        "using": plan.reference_of(plan.sort.by),
    })


#: ``_render_style``'s rule accumulator, as the helpers that feed it see it:
#: ``add(element, tag, attributes[, palette colours])``.
AddRule = Callable[..., None]


def _render_style(parent: ET.Element, plan: WorksheetPlan, tokens: DesignTokens) -> None:
    """Render the worksheet's ``<style>``, one style-rule per element, alphabetically.

    Tableau Desktop rewrites style rules into alphabetical order on save; emitting them any
    other way produces a diff on first open. Rules are collected into a dict keyed by
    element and then sorted, so a new rule cannot be added in the wrong place.
    """
    rules: dict[str, list[tuple[str, dict, tuple[str, ...]]]] = {}

    def add(element: str, tag: str, attributes: dict, colors: tuple[str, ...] = ()) -> None:
        rules.setdefault(element, []).append((tag, attributes, colors))

    if tokens.present:
        add("worksheet", "format", {"attr": "font-family", "value": tokens.font_family})

    # Field labels repeat what the zone's header already says, and Tableau reserves a whole
    # band of the sheet for them - on a dashboard that band is stolen from the chart.
    for scope in ("cols", "rows"):
        add("worksheet", "format", {
            "attr": "display-field-labels", "scope": scope, "value": "false",
        })

    for shelf, scope, references in (
        ("rows", "rows", plan.rows), ("columns", "cols", plan.columns)
    ):
        title = plan.axis_titles.get(shelf)
        if not (title and references):
            continue
        add("axis", "format", {
            "attr": "title", "class": "0",
            "field": plan.reference_of(references[0]),
            "scope": scope, "value": title,
        })

    for reference, pattern in plan.number_formats:
        add("cell", "format", {
            "attr": "text-format",
            "field": plan.reference_of(reference),
            "value": pattern,
        })

    _add_palette(add, plan, tokens)
    _add_sheet_format(add, plan)

    if plan.spec.geographic:
        add("map", "format", {"attr": "washout", "value": "0.0"})

    if plan.spec.dual and len(plan.rows) > 1:
        # Sync the two axes and hide the second one's labels: without this the chart draws
        # two independent scales and reads as two unrelated series.
        second = plan.reference_of(plan.rows[1])
        add("axis", "encoding", {
            "attr": "space", "class": "0", "field": second, "field-type": "quantitative",
            "fold": "true", "scope": "rows", "synchronized": "true", "type": "space",
        })
        add("axis", "format", {
            "attr": "display", "class": "0", "field": second, "scope": "rows",
            "value": "false",
        })

    style = ET.SubElement(parent, "style")
    for element in sorted(rules):
        rule = ET.SubElement(style, "style-rule", {"element": element})
        for tag, attributes, colors in rules[element]:
            node = ET.SubElement(rule, tag, attributes)
            if colors:
                # A ramp is an ordered palette; a categorical one is 'regular'.
                palette = ET.SubElement(node, "color-palette", {
                    "custom": "true",
                    "name": BRAND_PALETTE_NAME,
                    "type": ("ordered-sequential"
                             if attributes.get("type") == "interpolated" else "regular"),
                })
                for color in colors:
                    ET.SubElement(palette, "color").text = color


def _add_palette(add: AddRule, plan: WorksheetPlan, tokens: DesignTokens) -> None:
    """Add the brand palette to whatever the worksheet colours its marks by.

    The colours ride along inline (see :class:`DesignTokens`), so no data member has to be
    known. Three shapes, decided by what is on Colour: a *dimension* takes the whole ordered
    palette and Tableau walks the domain against it; a *measure* is a continuous ramp, and a
    ramp has two ends - the first and last brand colours, low to high; *nothing* on Colour has
    no domain at all, so it gets the brand's first colour as the flat mark colour.

    Args:
        add: ``_render_style``'s rule accumulator.
        plan: The worksheet plan.
        tokens: The design tokens.
    """
    if not tokens.series_colors:
        return
    if plan.spec.dual:
        # A dual chart colours its two measures apart by Measure Names - a dimension, whatever
        # the measures are.
        field_reference, quantitative = plan.qualify(MEASURE_NAMES), False
    elif "color" in plan.encodings:
        reference = plan.encodings["color"]
        field_reference = plan.reference_of(reference)
        quantitative = reference.instance_type == "quantitative"
    else:
        # Nothing on Colour, so there is no domain to walk - but the marks still have *a*
        # colour, and Tableau's is its default blue. The brand's first series colour is what
        # keeps a plain bar or line from reading as "generated" too.
        add("mark", "format", {"attr": "mark-color", "value": tokens.series_colors[0]})
        return

    colors = tokens.series_colors
    if quantitative:
        colors = (colors[0], colors[-1])
    # ponytail: XSD-legal, but only Desktop can confirm it keeps the palette on save rather
    # than rewriting it (the `custom` flag and the name<->`palette` pairing are inferred from
    # the schema, not read out of a Desktop-saved workbook). Issue #52 carries the steps; a
    # wrong guess costs a rewrite on open, not a broken workbook.
    add("mark", "encoding", {
        "attr": "color",
        "field": field_reference,
        "palette": BRAND_PALETTE_NAME,
        "type": "interpolated" if quantitative else "palette",
    }, colors)


def _add_sheet_format(add: AddRule, plan: WorksheetPlan) -> None:
    """Add the Format Borders / Lines / Shading / Alignment rules the manifest asked for.

    A KPI card centres itself unless the format says otherwise - its whole treatment is one big
    centred number, and an explicit ``align`` is the analyst overruling that.

    ponytail: every attribute here is in the XSD's ``StyleAttribute-ST``, but which *element*
    Desktop hangs each one off is inferred (``text-align`` / ``vertical-align`` on ``cell`` are
    the exceptions - the KPI card has round-tripped them). Issue #52 carries the save-and-diff
    steps; the cost of a wrong pairing is a format that does not show, not a broken workbook.

    Args:
        add: ``_render_style``'s rule accumulator.
        plan: The worksheet plan.
    """
    sheet_format = plan.sheet_format

    if sheet_format.shading:
        add("pane", "format", {"attr": "background-color", "value": sheet_format.shading})

    if sheet_format.borders == NO_FORMAT:
        add("cell", "format", {"attr": "border-style", "value": NO_FORMAT})
        add("cell", "format", {"attr": "border-width", "value": "0"})
    elif sheet_format.borders:
        add("cell", "format", {"attr": "border-color", "value": sheet_format.borders})
        add("cell", "format", {"attr": "border-style", "value": BORDER_STYLE})
        add("cell", "format", {"attr": "border-width", "value": BORDER_WIDTH})

    for element, value in (
        ("gridline", sheet_format.gridlines), ("zeroline", sheet_format.zero_lines)
    ):
        if value == NO_FORMAT:
            add(element, "format", {"attr": "display", "value": "false"})
        elif value:
            add(element, "format", {"attr": "stroke-color", "value": value})

    centred = "center" if plan.spec.kpi_card else ""
    for attribute, value in (
        ("text-align", sheet_format.align or centred),
        ("vertical-align", sheet_format.vertical_align or centred),
    ):
        if value:
            add("cell", "format", {"attr": attribute, "value": value})


def _render_panes(parent: ET.Element, plan: WorksheetPlan, tokens: DesignTokens) -> None:
    """Render the worksheet's panes: one, or three for a dual-axis / combo chart."""
    panes = ET.SubElement(parent, "panes")
    if plan.spec.dual and len(plan.rows) > 1:
        # Pane 0 is the shared default; panes 1..n each own one measure's axis, in the same
        # order the measures sit on Rows.
        # ponytail: a dual chart's reference lines go on the shared pane, not per axis - one
        # line per measure needs the manifest to say which axis, which no spec has asked for.
        _render_pane(panes, plan, tokens, {}, plan.spec.mark_class, True)
        for index, reference in enumerate(plan.rows):
            mark = (
                plan.spec.pane_marks[index]
                if index < len(plan.spec.pane_marks) else plan.spec.mark_class
            )
            _render_pane(
                panes, plan, tokens,
                {
                    "id": str(index + 1),
                    "y-axis-name": plan.reference_of(reference),
                },
                mark,
            )
        return
    _render_pane(panes, plan, tokens, {}, plan.spec.mark_class, True)


def _render_pane(
    parent: ET.Element, plan: WorksheetPlan, tokens: DesignTokens,
    attributes: dict, mark_class: str, with_reference_lines: bool = False,
) -> None:
    """Render one pane: its mark class, encodings, reference lines, tooltip, label and style.

    The child order is the XSD's ``PaneSpecification-G`` sequence - view, mark, encodings,
    reference-line, customized-tooltip, customized-label, style - not the order they were
    decided in. (The legacy ``FEATURES.md`` puts ``reference-line`` in ``<view>``; the XSD and
    Tableau's own output both put it here.)
    """
    pane = ET.SubElement(
        parent, "pane", dict(sorted({**attributes, **PANE_RELAXATION}.items()))
    )
    ET.SubElement(ET.SubElement(pane, "view"), "breakdown", {"value": "auto"})
    ET.SubElement(pane, "mark", {"class": mark_class})

    # {encoding: qualified column}. Two encodings are built-ins the manifest never names:
    # a dual chart colours its two measures apart with Measure Names, and a map needs the
    # generated geometry to draw its polygons.
    columns = {
        name: plan.reference_of(reference)
        for name, reference in plan.encodings.items()
    }
    if plan.spec.dual:
        columns["color"] = plan.qualify(MEASURE_NAMES)
    if plan.spec.geographic:
        columns["geometry"] = plan.qualify(GENERATED_GEOMETRY)

    if columns or plan.tooltip or plan.detail:
        block = ET.SubElement(pane, "encodings")
        for name in ENCODING_ORDER:
            if name in columns:
                ET.SubElement(block, name, {"column": columns[name]})
        # Detail-shelf fields carry no visual role - <lod> is repeatable, so a zone's
        # visibility field joins whatever the manifest put on Detail.
        for reference in plan.detail:
            ET.SubElement(block, "lod", {"column": plan.reference_of(reference)})
        # Every field the tooltip template names must also be registered as an encoding,
        # or Tableau has no value to substitute into it.
        for _, reference in plan.tooltip:
            ET.SubElement(block, "tooltip", {
                "column": plan.reference_of(reference)
            })

    if with_reference_lines:
        _render_reference_lines(pane, plan)
    if plan.tooltip:
        _render_tooltip(pane, plan, tokens)
    if plan.spec.kpi_card and "text" in plan.encodings:
        _render_big_number(pane, plan, tokens)
    # A field on Text *is* the request for mark labels, on any chart type - a bar with SUM on
    # Text means labelled bars. It is also what makes 'number_formats' visible: the cell format
    # only reaches a chart through its labels, which is why a styled bar with no label showed
    # nothing for its format.
    if plan.spec.label_marks or "text" in plan.encodings:
        style = ET.SubElement(pane, "style")
        rule = ET.SubElement(style, "style-rule", {"element": "mark"})
        for attribute in ("mark-labels-show", "mark-labels-cull"):
            ET.SubElement(rule, "format", {"attr": attribute, "value": "true"})


def _render_reference_lines(parent: ET.Element, plan: WorksheetPlan) -> None:
    """Render the pane's reference lines.

    A line's axis and value are the same field: a reference line summarises the measure whose
    axis it is drawn on, and both references must be **fully qualified** (unlike the
    unqualified ``level`` a filter carries).

    Args:
        parent: The ``<pane>`` element.
        plan: The worksheet plan.
    """
    for index, line in enumerate(plan.reference_lines):
        column = plan.reference_of(line.reference)
        attributes = {
            "axis-column": column,
            "enable-instant-analytics": "true",
            "formula": line.formula,
            "id": f"refline{index}",
            "label-type": line.label_type,
            # Desktop writes the confidence probability on every reference line, whatever the
            # formula, and adds it on save when it is missing.
            "probability": REFERENCE_LINE_PROBABILITY,
            "scope": line.scope,
            "value-column": column,
            "z-order": "1",
        }
        if line.label:
            attributes["label"] = line.label
        ET.SubElement(parent, "reference-line", dict(sorted(attributes.items())))


def _render_tooltip(
    parent: ET.Element, plan: WorksheetPlan, tokens: DesignTokens
) -> None:
    """Render a ``<customized-tooltip>``: one ``label: value`` line per tooltip pair.

    The break goes *between* pairs only - a break after the label pushed every value onto
    its own line, which is what made the rendered tooltip look double-spaced.
    """
    formatted = ET.SubElement(
        ET.SubElement(parent, "customized-tooltip"), "formatted-text"
    )
    for index, (label, reference) in enumerate(plan.tooltip):
        if index:  # separate this pair from the previous one
            _render_run(formatted, TOOLTIP_BREAK, tokens, tokens.title_size)
        _render_run(formatted, f"{label}: ", tokens, tokens.title_size, bold=True)
        _render_run(
            formatted,
            cdata(f"<{plan.reference_of(reference)}>"),
            tokens, tokens.title_size, bold=True,
        )


def _render_big_number(parent: ET.Element, plan: WorksheetPlan, tokens: DesignTokens) -> None:
    """Render a KPI card's ``<customized-label>`` - the text encoding, set large."""
    formatted = ET.SubElement(
        ET.SubElement(parent, "customized-label"), "formatted-text"
    )
    _render_run(
        formatted,
        cdata(f"<{plan.reference_of(plan.encodings['text'])}>"),
        tokens, tokens.kpi_size, tokens.title_color, bold=True,
    )


def _shelf_text(plan: WorksheetPlan, references: list[FieldRef], generated: str) -> str:
    """Render one shelf's text from its resolved fields.

    Args:
        plan: The worksheet plan.
        references: The shelf's fields, in manifest order.
        generated: The Tableau-generated field this shelf carries on a map.

    Returns:
        The shelf string: empty for a pie/KPI card, ``(a + b)`` for a dual axis, ``(a / b)``
        for nested dimensions, and the single qualified reference otherwise.
    """
    if plan.spec.geographic:
        return plan.qualify(generated)
    if plan.spec.empty_shelves or not references:
        return ""
    qualified = [plan.reference_of(reference) for reference in references]
    if len(qualified) == 1:
        return qualified[0]
    # '+' overlays measures on one axis pair (dual axis / combo); '/' nests dimensions.
    separator = " + " if plan.spec.dual else " / "
    return "(" + separator.join(qualified) + ")"


def render_worksheet(parent: ET.Element, plan: WorksheetPlan, tokens: DesignTokens,
                     simple_id: str) -> None:
    """Render one complete ``<worksheet>`` from its plan.

    Args:
        parent: The ``<worksheets>`` element.
        plan: The resolved worksheet.
        tokens: The design tokens (Tableau defaults when the file was absent).
        simple_id: The braced UUID for the sheet's ``<simple-id>``.
    """
    element = ET.SubElement(parent, "worksheet", {"name": plan.name})
    if tokens.present:
        _render_title(element, tokens)

    table = ET.SubElement(element, "table")
    view = ET.SubElement(table, "view")
    datasources = ET.SubElement(view, "datasources")
    ET.SubElement(datasources, "datasource", {
        "caption": plan.resolver.datasource_caption,
        "name": plan.resolver.datasource_id,
    })
    if plan.parameters:
        # A calculation that reads a parameter is unresolvable unless the view declares the
        # Parameters datasource too - Tableau opens the sheet with the field greyed out.
        ET.SubElement(datasources, "datasource", {"name": PARAMETERS_DATASOURCE})
    if plan.spec.geographic:
        ET.SubElement(
            ET.SubElement(view, "mapsources"), "mapsource", {"name": MAPSOURCE_NAME}
        )
    if plan.parameters:
        parameter_dependencies = ET.SubElement(
            view, "datasource-dependencies", {"datasource": PARAMETERS_DATASOURCE}
        )
        for parameter in plan.parameters:
            render_parameter_column(parameter_dependencies, parameter, with_domain=False)
    _render_dependencies(view, plan)
    filtered = _render_filters(view, plan)
    _render_sort(view, plan)
    if filtered:
        # Every filtered field must appear here or Tableau silently drops the filter.
        slices = ET.SubElement(view, "slices")
        for column in filtered:
            ET.SubElement(slices, "column").text = column
    ET.SubElement(view, "aggregation", {"value": "true"})

    _render_style(table, plan, tokens)
    _render_panes(table, plan, tokens)
    ET.SubElement(table, "rows").text = _shelf_text(plan, plan.rows, GENERATED_LATITUDE)
    ET.SubElement(table, "cols").text = _shelf_text(plan, plan.columns, GENERATED_LONGITUDE)

    # Both of these come after the shelves in the XSD's <table> sequence.
    bins = bin_columns(plan)
    if bins:
        # Show every bin range, including the empty ones - a histogram with gaps silently
        # dropped misreads as a different distribution.
        full_range = ET.SubElement(table, "show-full-range")
        for reference in bins:
            ET.SubElement(full_range, "column").text = plan.qualify(reference.column_name)
    if plan.spec.kpi_card:
        # A KPI card is a single number, not a mark worth hovering.
        ET.SubElement(table, "tooltip-style", {"tooltip-mode": "none"})

    ET.SubElement(element, "simple-id", {"uuid": simple_id})
