---
name: xlsx
description: Read, create, or edit Excel spreadsheets (.xlsx/.xlsm) — sheet data,
  formulas, styles, charts, multi-sheet workbooks — and bulk .csv/.tsv tables; use
  whenever a spreadsheet is the input or the deliverable (extract/analyze data, add
  columns/formulas/formatting/charts, clean messy tables, build from scratch), but
  not for Google Sheets API or Word/PDF/script outputs.
tags:
- tool
- office
requires:
  sandbox: shell
---

# Excel (.xlsx) workbooks

## Runtime

Use `exec` with complete Python source (`language: python`). Prefer creating,
saving, reopening, and validating the workbook in one call; later calls can
revise the same relative filename. Follow the turn's **User workspace**
instructions for locating inputs, output boundaries, and presenting the
finished file.

Use **openpyxl** for cells, formulas, styles, charts, merged cells, multi-sheet
workbooks, number formats, and streaming large sheets. It is declared by every
supported DeepTutor installation. Do not assume pandas is installed: it exists
in the Docker runner but is not a direct dependency of every pip/source install.

## Where to save, and how to deliver the file

**The `exec` working directory is already the servable output directory.** Every
`exec` call starts in `data/user/workspace/chat/chat/<turn>/exec`, which the
server serves - so `wb.save("out.xlsx")` lands the file where the user can reach it
*and* makes it downloadable. Six rules:

1. **Save with a bare filename.** `wb.save("out.xlsx")` and you are done.
2. **Never build a path from `DEEPTUTOR_WORKSPACE_ROOT`.** It points at
   `data/user`, which is *not* servable (the `workspace/` level is missing):
   files written there save fine and still 404 on download.
3. **An explicit path must stay under the cwd and keep the `exec` kind
   segment.** The server serves the turn's own output directory -
   `workspace/chat/chat/<turn>/exec/...` or
   `workspace/outputs/chat/<session>/<turn>/exec/...` - and nothing else, so
   `<cwd>/subdir/out.xlsx` is safe while an absolute path built elsewhere is not.
4. **Create the directory before saving.** `save()` never creates directories,
   so saving into a missing one raises `FileNotFoundError: [Errno 2]`. Make it
   first: `Path("subdir").mkdir(parents=True, exist_ok=True)` (or `os.makedirs`).
5. **Deliver with `workspace_present`.** Call it with the file's
   workspace-relative path and use the relative path it returns, copied
   exactly, in your Markdown link. The served prefix is `/files/outputs/<path>`;
   never hand-write `/chat/outputs/...` - a hand-assembled link 404s even when
   the file is correct.
6. **Verify before presenting.** If a download 404s, the fix is the **path**,
   not the file: check the file exists and that its path matches a served shape.

```python
# WRONG - DEEPTUTOR_WORKSPACE_ROOT is data/user: the file exists but 404s
import os
out = os.environ["DEEPTUTOR_WORKSPACE_ROOT"] + "/chat/unified_x/turn_y"
wb.save(out + "/out.xlsx")
# RIGHT - bare filename in the current (servable) working directory
wb.save("out.xlsx")
# RIGHT - explicit path under the cwd, with the directory created first
from pathlib import Path
sub = Path("deliverables")
sub.mkdir(parents=True, exist_ok=True)
wb.save(sub / "out.xlsx")
```

## THE critical gotcha: openpyxl writes formulas but never computes them

`ws["B10"] = "=SUM(B2:B9)"` stores the formula *string*. openpyxl has no formula
engine — the cached value stays empty (or stale, on an edited file). So:

- A workbook you create/edit with openpyxl opens fine in Excel/LibreOffice (they
  recompute on open), but its cached values are wrong until then.
- Anything reading cached values first — `data_only=True`, another
  pandas/openpyxl pass, or a downstream tool — sees blanks/stale data.

Pick by what the deliverable needs:

1. **Static numbers (most common).** If the user just needs correct values and
   the sheet need not stay live, compute in Python and write the **number**, not
   a formula string: `ws["B10"] = sum(c.value for c in ws["B2:B9"][0])`. Correct
   immediately, no recalc needed.
2. **Live model** (formulas that recompute on the user's later edits). Write real
   formulas, and reference cells not literals (`=B5*(1+$B$6)`, not `=B5*1.05`).
   openpyxl can't set the cached value too. If `shutil.which("soffice")` succeeds,
   recalculate through `exec` using `subprocess.run` and a
   relative `_recalc/` directory, replace `out.xlsx` with the recalculated copy,
   then remove `_recalc/`. Never use `/tmp` or search for a desktop installation.
   A later exec call can see the same bare filename. If LibreOffice is
   absent, warn that formulas populate when the user opens the file in Excel.

## Reading

```python
from openpyxl import load_workbook

wb = load_workbook("in.xlsx", read_only=True, data_only=False)
for sheet_name in wb.sheetnames:
    ws = wb[sheet_name]
    for row in ws.iter_rows(values_only=True):
        print(row)
```

To read **computed results** of formulas (not the formula text), use openpyxl
with `data_only=True` — returns the value Excel last cached:

```python
from openpyxl import load_workbook

wb = load_workbook("in.xlsx", data_only=True)
val = wb["Sheet1"]["B10"].value  # None if Excel never opened/saved the file
```

Gotcha: never `save()` a workbook loaded with `data_only=True` — that discards
every formula permanently (verified: the cell becomes `None`). Load twice if you
need both formulas and values.

Large file: `load_workbook(path, read_only=True)` streams rows cheaply.

## Creating

```python
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment

wb = Workbook()
ws = wb.active
ws.title = "Summary"
ws.append(["Region", "Sales"])  # header row
for r in [("West", 120), ("East", 95)]:
    ws.append(r)
ws["B4"] = "=SUM(B2:B3)"  # see formula gotcha above

ws["A1"].font = Font(bold=True)
ws["A1"].fill = PatternFill("solid", fgColor="DDDDDD")
ws["A1"].alignment = Alignment(horizontal="center")
ws["B2"].number_format = "#,##0"  # thousands separator
ws.column_dimensions["A"].width = 18
ws.freeze_panes = "A2"  # freeze header
wb.create_sheet("Detail")  # second sheet
wb.save("out.xlsx")

# Validate immediately; later exec calls can also reopen this relative path.
check = load_workbook("out.xlsx", data_only=False)
assert check.sheetnames, "generated workbook has no worksheets"
import zipfile

with zipfile.ZipFile("out.xlsx") as package:
    assert package.testzip() is None, "generated XLSX has a corrupt ZIP member"
```

For large exports, use openpyxl's write-only mode and append rows without
holding every cell object in memory:
```python
from openpyxl import Workbook

wb = Workbook(write_only=True)
ws = wb.create_sheet("Data")
ws.append(["id", "value"])
for row in rows:
    ws.append(row)
wb.save("out.xlsx")
```

## Editing (preserve existing formatting)

`load_workbook` keeps styles, formulas, merged cells, charts intact — edit only
what you touch. Do NOT round-trip through pandas to preserve formatting (pandas
rewrites the whole sheet, losing styles).

```python
from openpyxl import load_workbook

wb = load_workbook("in.xlsx")  # keep formulas (data_only=False)
ws = wb["Sheet1"]
ws["C2"] = "Updated"
wb.save("out.xlsx")  # preserve the source; present the new file
```

Match the file's existing conventions (font, number formats, colors) rather than
imposing new ones — an established template wins over any default.

When inserting/deleting rows or columns (`ws.insert_rows`, `ws.delete_cols`),
openpyxl does **not** rewrite formulas that reference shifted cells. Re-point
affected formulas yourself, or avoid structural shifts in formula-heavy sheets.

## Charts

```python
from openpyxl.chart import BarChart, Reference

ch = BarChart()
ch.title = "Sales"
data = Reference(ws, min_col=2, min_row=1, max_row=3)  # include header for title
cats = Reference(ws, min_col=1, min_row=2, max_row=3)
ch.add_data(data, titles_from_data=True)
ch.set_categories(cats)
ws.add_chart(ch, "E2")
```
LineChart / PieChart / ScatterChart follow the same shape.

## Verifying you produced clean output

In the same `exec` Python call, reload and scan for error strings after writing.
These mean broken formulas
that recalc surfaced (`#REF!` bad reference, `#DIV/0!` zero denominator,
`#VALUE!` type mismatch, `#NAME?` unknown function, `#N/A`):

```python
from openpyxl import load_workbook

wb = load_workbook("out.xlsx", data_only=True)
errs = [
    f"{s}!{c.coordinate}={c.value}"
    for s in wb.sheetnames
    for row in wb[s].iter_rows()
    for c in row
    if isinstance(c.value, str) and c.value.startswith("#")
]
print(errs or "clean")
```
This only catches errors in *cached* values. If you wrote formulas and couldn't
recalc (no soffice), cached values are blank, so the check is meaningful only
after a recalc or after Excel opens the file. Writing computed numbers (option 1)
sidesteps this.

## CSV / TSV

```python
import csv

with open("in.csv", newline="", encoding="utf-8-sig") as source:
    rows = list(csv.reader(source))  # delimiter="\t" for TSV
with open("out.csv", "w", newline="", encoding="utf-8") as target:
    csv.writer(target).writerows(rows)
```
For messy input (junk rows, header not on row 1, ragged columns), inspect a
bounded sample and explicitly normalize only the requested rows/columns.

## Raw OOXML (rarely needed)

openpyxl covers essentially all xlsx features; reach for raw XML only for the
narrow cases it can't express (e.g. preserving an exotic part it drops on
re-save). An .xlsx is a ZIP: `xl/workbook.xml`, `xl/worksheets/sheet1.xml`,
`xl/sharedStrings.xml`, plus `[Content_Types].xml` and `_rels/`. Unzip with
stdlib `zipfile`, edit the part, re-zip — keep `[Content_Types].xml` and every
`.rels` consistent, keep IDs unique, and don't pretty-print into value-bearing
text nodes. Correctness check = it opens in Excel with no repair prompt.
