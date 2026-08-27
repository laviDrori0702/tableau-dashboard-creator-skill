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
for issue #50. Four sheets, one calculation each, identical base setup: `Order Date`
(continuous Month) on Columns, `SUM(Sales)` on Rows, `Sample - Superstore`.

`tests/test_features.py::test_the_attested_prefixes_match_desktops_own_output` parses it and
asserts `FieldRef.instance_name` reproduces Desktop's `<rows>` shelf byte for byte, so the
table cannot drift from this file without a test failing.

| sheet | `<table-calc type>` | Desktop's instance name | prefix |
|---|---|---|---|
| `1-total` | `CumTotal` | `[cum:sum:Sales:qk]` | `cum` |
| `2-difference` | `Difference` | `[diff:sum:Sales:qk]` | `diff` |
| `3-percent-from` | `PctValue` | `[pcva:sum:Sales:qk]` | `pcva` |
| `4-percentile` | `PctRank` | `[pcrk:sum:Sales:qk]` | `pcrk` |

Sheet `1-total` is named for the *requested* calculation but carries **Running Total** -
`type='CumTotal'`. It re-attests `CumTotal` and leaves `WindowTotal` unsettled.

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

`WindowTotal` (`wnd`) is the last guess. It appears in no workbook checked. To settle it:
apply **Add Table Calculation -> Calculation Type = Total** to a `SUM(Sales)` pill, save as
`.twb`, and read the `<rows>` shelf.

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
