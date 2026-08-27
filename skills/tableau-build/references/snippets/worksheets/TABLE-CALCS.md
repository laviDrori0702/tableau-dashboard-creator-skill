# Table-calculation instance-name prefixes

A table calculation does not get a `<column>` of its own. It lives on the
`<column-instance>` as a `<table-calc>` child, and Tableau puts an extra prefix on the
instance `name`. `worksheet.TABLE_CALC_PREFIXES` holds that prefix per `TCType-ST` value.

The prefix is cosmetic: a wrong one makes Desktop rewrite the name on open, it does not
refuse the workbook. That is why a guess survives both validators - only a Desktop-saved
workbook settles it. Of the eight prefixes, five were originally guessed and **three of
those five were wrong**, which is the measure of how little the naming can be inferred.

**The percent family is `pc` + two letters - four characters.** `pcto`, `pcdf`, `pcva`,
`pcrk`. Not `pct` + verb, which is the shape every guess reached for. The non-percent calcs
have no shared shape at all: `cum`, `diff`, `rank`.

## `table-calculations-attestation.twb`

The workbook beside this file, authored in **Desktop 2025.1.10 (20251.25.1121.1650), win**,
for issue #50. Five sheets, one calculation each, identical base setup: `Order Date`
(continuous Month) on Columns, `SUM(Sales)` on Rows, `Sample - Superstore`.

`tests/test_features.py::test_the_attested_prefixes_match_desktops_own_output` parses it and
asserts `FieldRef.instance_name` reproduces Desktop's `<rows>` shelf, so the table cannot
drift from this file without a test failing.

| sheet | applied as | `<table-calc type>` | Desktop's instance name | prefix |
|---|---|---|---|---|
| `1a-total` | `TOTAL(sum([Sales]))` | *absent* | `[usr:Calculation_1660139451271188481:qk]` | *none* |
| `1b-running-total` | Running Total | `CumTotal` | `[cum:sum:Sales:qk]` | `cum` |
| `2-difference` | Difference | `Difference` | `[diff:sum:Sales:qk]` | `diff` |
| `3-percent-from` | Percent From | `PctValue` | `[pcva:sum:Sales:qk]` | `pcva` |
| `4-percentile` | Percentile | `PctRank` | `[pcrk:sum:Sales:qk]` | `pcrk` |

```xml
<!-- 2-difference -->
<column-instance column='[Sales]' derivation='Sum' name='[diff:sum:Sales:qk]' pivot='key' type='quantitative'>
  <table-calc diff-options='Relative' ordering-type='Rows' type='Difference'>

<!-- 3-percent-from -->
<column-instance column='[Sales]' derivation='Sum' name='[pcva:sum:Sales:qk]' pivot='key' type='quantitative'>
  <table-calc diff-options='Relative' ordering-type='Rows' type='PctValue'>

<!-- 4-percentile -->
<column-instance column='[Sales]' derivation='Sum' name='[pcrk:sum:Sales:qk]' pivot='key' type='quantitative'>
  <table-calc ordering-type='Rows' rank-options='Competition,Ascending' type='PctRank' />
```

### A window total has no type and no prefix

Sheet `1a-total` is the important negative result. Desktop's **Total** is not a
Quick Table Calculation - it is authored as a **calculated field**, and the
`<table-calc>` it carries has **no `type` attribute at all**, only addressing:

```xml
<column caption='Total Sales' datatype='real' name='[Calculation_1660139451271188481]' role='measure' type='quantitative'>
  <calculation class='tableau' formula='TOTAL(sum([Sales]))'>
    <table-calc ordering-type='Rows' />
  </calculation>
</column>
<column-instance column='[Calculation_1660139451271188481]' derivation='User' name='[usr:Calculation_1660139451271188481:qk]' pivot='key' type='quantitative'>
  <table-calc ordering-type='Rows' />
</column-instance>
```

The instance keeps the plain `usr` prefix a calculated field always gets - it gains no
table-calc prefix. So the builder's way to express a window total is a **calculated field
with the `TOTAL(...)` formula**, not `table_calc: WindowTotal`.
`test_a_total_table_calc_is_a_calculated_field_with_no_type` pins this.

### The Calculation Type menu, mapped

Desktop 2025.1.10's **Add Table Calculation -> Calculation Type** dropdown offers eight
entries. Seven map to an attested `TCType-ST`; there is **no "Total" entry**.

| menu entry | `TCType-ST` | prefix |
|---|---|---|
| Difference From | `Difference` | `diff` |
| Percent Difference From | `PctDiff` | `pcdf` |
| Percent From | `PctValue` | `pcva` |
| Percent of Total | `PctTotal` | `pcto` |
| Rank | `Rank` | `rank` |
| Percentile | `PctRank` | `pcrk` |
| Running Total | `CumTotal` | `cum` |
| Moving Calculation | *unchecked* - the `WindowTotal` candidate | *unknown* |

## Attested from the wider corpus

Three more come from a 195-workbook sweep of the local corpus (`~/Downloads`,
`~/Documents/My Tableau Repository/Workbooks`, this repo). Fragments verbatim; the
`source-build` is that workbook's own. All are `version='18.1'`.

### `PctDiff` -> `pcdf`

`20250818-appsfortableau_HierarchyFilter demo workbook-demo-workbook.twbx`, 2024.3.0, mac.

```xml
<column-instance column='[Sales]' derivation='Sum' name='[pcdf:sum:Sales:qk]' pivot='key' type='quantitative'>
  <table-calc diff-options='Relative' level-address='[Sample - Superstore].[yr:Order Date:ok]' ordering-field='[Sample - Superstore].[Order Date]' ordering-type='Field' type='PctDiff'>
    <address>
      <value>-1</value>
    </address>
  </table-calc>
</column-instance>
```

### `PctTotal` -> `pcto`

`lavi_webpage_test.twbx`, 2025.2.0. Also the prefix Desktop 2025.1 rewrote when the builder
saved its own guess - the original attestation.

```xml
<column-instance column='[Sales]' derivation='Sum' name='[pcto:sum:Sales:qk]' pivot='key' type='quantitative'>
```

### `Rank` -> `rank`

`Embedded Filters Test.twbx`, 2024.2.10, win.

```xml
<column-instance column='[Sales]' derivation='Sum' name='[rank:sum:Sales:qk]' pivot='key' type='quantitative'>
  <table-calc ordering-type='Columns' rank-options='Competition,Descending' type='Rank' />
</column-instance>
```

### Nesting

A percent-of-total *of* a running total nests both prefixes, outermost first:
`name='[pcto:cum:usr:LinPack_215363864742895389:qk]'` (`FINANCIAL SERVICES - Trading.twbx`,
2023.3.0) - which is what `FieldRef.instance_name` builds by prepending one prefix to
`self.prefix`.

## Still inferred

`WindowTotal` -> `wnd` is the last guess, and the weakest thing in this table. What is known:

- It is in the XSD's `TCType-ST`, so Tableau does write it somewhere.
- It appears in **none of the 196 workbooks** swept. Neither does `Custom`, the other
  `TCType-ST` value the builder does not offer, nor the `window-options` attribute.
- Desktop's `TOTAL(...)` - the function an analyst means by "window total" - does **not**
  produce it. See the negative result above.
- Of the eight Calculation Type entries, **Moving Calculation** is the only one not yet
  traced to a type. `WindowOptions-ST` is `IncludeCurrent` / `NullIfIncomplete`, which are
  exactly Moving Calculation's two checkboxes, and `window-options` sits beside `type` on
  the same element. That makes Moving Calculation the strong candidate for `WindowTotal`.

To settle it: apply **Quick Table Calculation -> Moving Average** (or **Add Table
Calculation -> Moving Calculation**) to a `SUM(Sales)` pill, save as `.twb`, and read the
`<rows>` shelf. If it writes `WindowTotal`, correct the prefix and note that the type means
*moving calculation*, not *total*. If it writes something else, `WindowTotal` is unreachable
from the UI and should be dropped from the table and rejected at validation.

## How to attest a new one

The pairing is `<table-calc type='X'>` inside a `<column-instance name='[prefix:...]'>`, so
one sweep reads them all:

```python
BLOCK = re.compile(
    r"<column-instance\b[^>]*name='\[([^']*)'[^>]*>\s*"
    r"<table-calc\b((?:[^>]*?\s)?)type='([^']*)'", re.S)
# group(1).split(":")[0] is the prefix, group(3) is the TCType.
```

Match `type=` with a leading-space group or a lookbehind - a bare `\btype=` also matches
`ordering-type=`, which reports `Rows`/`Columns`/`Field` as calculation types.
