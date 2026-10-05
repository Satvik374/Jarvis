---
name: data-entry
description: Get information into a spreadsheet or table accurately, checking the
  row count before and after.
when_to_use: spreadsheet, excel, csv, enter data, fill the sheet, table, log, record
  entries, tabulate
tools: read_file, read_document, write_file, edit_file, python, run_command, open_app,
  click, type, observe, notify
version: '1.0'
created: '2026-09-26T21:39:29'
updated: '2026-09-26T21:39:29'
---

# Data entry

## Steps
1. **Learn the shape first.** `read_file` the existing sheet or CSV and read its
   header row. New rows must match the existing columns exactly, blank cells and
   all.
2. **Prefer a script over keystrokes.** For anything more than a few rows, use
   `python` or `run_command` to append/transform the file (or `edit_file` the CSV
   directly): it is faster and byte-exact. Reserve clicking and typing for an app
   that has no file behind it.
3. **Work on a copy or a new column** when the source data matters, and only
   replace the original once the result checks out.
4. **Verify with a count and a sample.** Rows before vs after, and re-read the
   first and last new row. The inserted row count is the cheapest proof that
   nothing was dropped.
5. **Report** how many rows were added and to which file/sheet.

## Rules
- Never overwrite a source file in place without a copy; a bad transform on the
  only copy is unrecoverable.
- Keep the original formatting and column order; do not "tidy" a sheet the user
  will hand to someone else.
