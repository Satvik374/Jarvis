---
name: organize-files
description: Tidy a folder by sorting files into clear categories, proposing the plan
  before moving anything.
when_to_use: organize, tidy, clean up, sort files, downloads folder, desktop clutter,
  rename files, file mess
tools: list_dir, find_files, make_dir, move_file, copy_file, read_file, notify, ask
version: '1.0'
created: '2026-09-26T21:39:29'
updated: '2026-09-26T21:39:29'
---

# Organizing a folder

## Steps
1. **Look before you move.** `list_dir` the target folder and read a sample of
   names to learn the real categories in this folder - "invoices" and "receipts"
   may be one job or two.
2. **Propose a short plan** and get it approved when the folder is not obvious
   junk: the target folders, roughly how many files each receives, and what
   happens to the leftovers. A tidy-up nobody agreed to is just a move nobody can
   find anything in afterwards.
3. **Create the folders** with `make_dir`, then `move_file` one category at a
   time, so a mistake is one category to undo.
4. **Never delete.** Unmatched files go into an `_unsorted` folder; the user
   decides their fate. Deleting to make a folder look tidy is a data-loss bug.
5. **Report** the counts moved per folder and name anything left unsorted.

## Rules
- Preserve file extensions and any dates in the name; a rename that loses the
  date makes a sorted folder worse than the original.
- If two files would collide at the destination, keep both - add a suffix rather
  than overwriting one with the other.
