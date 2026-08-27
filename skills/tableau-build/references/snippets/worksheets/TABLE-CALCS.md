# Table-calculation instance-name prefixes

A table calculation applied through **Add Table Calculation** does not get a `<column>` of
its own. It lives on the `<column-instance>` as a `<table-calc>` child, and Tableau puts an
extra prefix on the instance `name`. `worksheet.TABLE_CALC_PREFIXES` holds that prefix per
`TCType-ST` value.

The prefix is cosmetic: a wrong one makes Desktop rewrite the name on open, it does not
refuse the workbook. That is why a guess survives both validators - only a Desktop-saved
workbook settles it. **All eight are now attested.** Of the seven originally guessed, four
were wrong.

## Do not extend this table by analogy

That is the lesson of issue #50, and the four-of-seven failure rate is the evidence.

| type | the guess | Desktop |
|---|---|---|
| `CumTotal` | `cum` | `cum` |
| `Difference` | `diff` | `diff` |
| `Rank` | `rank` | `rank` |
| `PctDiff` | `pctdiff` | **`pcdf`** |
| `PctValue` | `pctval` | **`pcva`** |
| `PctRank` | `pctrank` | **`pcrk`** |
| `WindowTotal` | `wnd` | **`win`** |

A short type name passes through (`cum`, `diff`, `rank`). A long one is squeezed to four
characters with the vowels dropped first (`pcto`, `pcdf`, `pcva`, `pcrk`). `WindowTotal`
becomes `win` - neither rule predicts it. Read a new one off Desktop.

## Two authoring paths, only one of which takes a prefix

Both produce a table calculation. They serialize differently:

| | applied via **Add Table Calculation** | written as a **formula** |
|---|---|---|
| carrier | the `<column-instance>` | a calculated field `<column>` |
| `<table-calc type>` | present (`CumTotal`, `PctDiff`, ...) | **absent** - addressing only |
| instance prefix | the type's prefix (`cum:`, `pcdf:`, ...) | the ordinary `usr:` |
| in a manifest | `table_calc: <type>` | a calculated field's `formula` |

`TOTAL(...)`, `WINDOW_SUM(...)` and the rest of that family *are* table calculations; they
are simply not offered by the dialog, so they take the formula path. `TABLE_CALC_PREFIXES`
governs the dialog-driven ones only.
`tests/test_features.py::test_a_total_table_calc_is_a_calculated_field_with_no_type` pins
that boundary.

## `table-calculations-attestation.twb`

The workbook beside this file, authored in **Desktop 2025.1.10 (20251.25.1121.1650), win**,
for issue #50. Six sheets, one calculation each, identical base setup: `Order Date`
(continuous Month) on Columns, `SUM(Sales)` on Rows, `Sample - Superstore`.

`tests/test_features.py::test_the_attested_prefixes_match_desktops_own_output` parses it and
asserts `FieldRef.instance_name` reproduces Desktop's `<rows>` shelf, so the table cannot
drift from this file without a test failing.

| sheet | applied as | `<table-calc type>` | Desktop's instance name | prefix |
|---|---|---|---|---|
| `1a-total` | `TOTAL(sum([Sales]))` formula | *absent* | `[usr:Calculation_1660139451271188481:qk]` | *none* |
| `1b-running-total` | Running Total | `CumTotal` | `[cum:sum:Sales:qk]` | `cum` |
| `2-difference` | Difference | `Difference` | `[diff:sum:Sales:qk]` | `diff` |
| `3-percent-from` | Percent From | `PctValue` | `[pcva:sum:Sales:qk]` | `pcva` |
| `4-percentile` | Percentile | `PctRank` | `[pcrk:sum:Sales:qk]` | `pcrk` |
| `5-moving-average` | Moving Average | `WindowTotal` | `[win:sum:Sales:qk]` | `win` |

```xml
<!-- 1b-running-total -->
<column-instance column='[Sales]' derivation='Sum' name='[cum:sum:Sales:qk]' pivot='key' type='quantitative'>
  <table-calc aggregation='Sum' ordering-type='Rows' type='CumTotal' />

<!-- 2-difference -->
<column-instance column='[Sales]' derivation='Sum' name='[diff:sum:Sales:qk]' pivot='key' type='quantitative'>
  <table-calc diff-options='Relative' ordering-type='Rows' type='Difference'>

<!-- 3-percent-from -->
<column-instance column='[Sales]' derivation='Sum' name='[pcva:sum:Sales:qk]' pivot='key' type='quantitative'>
  <table-calc diff-options='Relative' ordering-type='Rows' type='PctValue'>

<!-- 4-percentile -->
<column-instance column='[Sales]' derivation='Sum' name='[pcrk:sum:Sales:qk]' pivot='key' type='quantitative'>
  <table-calc ordering-type='Rows' rank-options='Competition,Ascending' type='PctRank' />

<!-- 5-moving-average: WindowTotal is what Moving Calculation writes. 'from'/'to' are the
     window bounds and window-options is the "include current value" checkbox. -->
<column-instance column='[Sales]' derivation='Sum' name='[win:sum:Sales:qk]' pivot='key' type='quantitative'>
  <table-calc aggregation='Avg' from='-2' ordering-type='Rows' to='0' type='WindowTotal' window-options='IncludeCurrent' />
```

`1a-total` takes the formula path, so it has no `type` and no prefix:

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

### The Calculation Type menu, mapped

Desktop 2025.1.10's dropdown offers eight entries, all now traced. There is no "Total"
entry - that is the formula path above.

| menu entry | `TCType-ST` | prefix |
|---|---|---|
| Difference From | `Difference` | `diff` |
| Percent Difference From | `PctDiff` | `pcdf` |
| Percent From | `PctValue` | `pcva` |
| Percent of Total | `PctTotal` | `pcto` |
| Rank | `Rank` | `rank` |
| Percentile | `PctRank` | `pcrk` |
| Running Total | `CumTotal` | `cum` |
| Moving Calculation | `WindowTotal` | `win` |

## Attested from the wider corpus

Two prefixes are not in the reference workbook. They come from a 195-workbook sweep of
`~/Downloads`, `~/Documents/My Tableau Repository/Workbooks` and this repo. Fragments
verbatim; `source-build` is each workbook's own. All are `version='18.1'`.

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

### Nesting

A percent-of-total *of* a running total nests both prefixes, outermost first:
`name='[pcto:cum:usr:LinPack_215363864742895389:qk]'` (`FINANCIAL SERVICES - Trading.twbx`,
2023.3.0) - which is what `FieldRef.instance_name` builds by prepending one prefix to
`self.prefix`.

## The two TCType-ST values the builder does not offer

`TCType-ST` enumerates ten values. `TABLE_CALC_PREFIXES` holds eight:

- **`None`** - "no table calc", so there is nothing to prefix.
- **`Custom`** - appears in none of the 196 workbooks swept, and no dropdown entry produces
  it. Unattested, so `manifest.TABLE_CALCS` rejects it rather than guessing a prefix.

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
