---
name: long-form-drafting
description: Draft a long document with real structure, then revise it in place instead
  of rewriting.
when_to_use: write a document, draft, report, essay, article, documentation, proposal,
  edit my writing
tools: write_file, edit_file, read_document, list_dir
version: '1.0'
created: '2026-09-18T14:52:34'
updated: '2026-09-18T14:52:34'
---

# Long-form drafting

## Steps
1. Fix the contract before writing a word, and say it back to the user for a
   correction: **audience**, **length**, **what it must achieve**, **format**.
   Half of bad long documents are a mismatch here, not bad prose.
2. Write the outline as headings only, and check it before filling it in. Each
   heading should be a claim, not a category: "Costs fall after year two" beats
   "Costs".
3. Draft section by section with `write_file`. Do not write the whole thing in
   one call - a document you cannot inspect is one you cannot fix.
4. Revise with `edit_file` so the file keeps its identity and the user can see
   the change. Re-reading with `read_document` before an edit beats trusting
   memory of what you wrote two steps ago.
5. Finish with a pass for the things that are cheap to miss: does the opening
   promise what the body delivers, does each section earn its length, and does
   the ending say what happens next.

## Rules
- Never pad to reach a length. A short document that answers the question is the
  deliverable; an inflated one is the complaint.
- Keep the user's voice and terminology when they gave you a draft to extend.
- Do not invent specifics - numbers, dates, names, quotations. Mark a gap as a
  gap and let the user fill it.
