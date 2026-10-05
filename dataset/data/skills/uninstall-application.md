---
name: uninstall-application
description: Remove a program cleanly - confirm which one, then check that it is gone
  instead of leaving it half-removed.
when_to_use: uninstall, remove the app, delete program, get rid of, reinstall, clean
  uninstall, no longer needed
tools: run_command, open_app, focus_window, click, type, press, observe, wait_for,
  system_diagnostics, notify, ask
version: '1.0'
created: '2026-09-26T21:39:29'
updated: '2026-09-26T21:39:29'
---

# Uninstalling an application

## Steps
1. **Confirm the exact program.** Names collide ("Acrobat" vs "Acrobat
   Reader"). Read the installed list first: `run_command` `winget list`, and ask
   the user to choose when more than one candidate matches.
2. **Uninstall from the terminal when possible:**
   `winget uninstall --id <Vendor.App> -e`. It removes the package without
   depending on finding the right window.
3. **Otherwise use the system uninstaller.** Open *Add or remove programs*
   (Settings > Apps > Installed apps), locate the entry, choose Uninstall, and
   step through the wizard with `click`/`press`, `wait_for`ing each window.
4. **Clean up only what you are sure about.** Leftover folders in `%APPDATA%` or
   `Program Files` may hold the user's files, not just the program's own files.
   Ask before deleting anything that is not clearly install output.
5. **Verify:** re-run `winget list` (or reopen the installed list) and show that
   the entry is gone before reporting success.
6. **Report** what was removed and anything you deliberately left behind.

## Rules
- Removing an application can take its data with it. Say so before starting when
  the user has used the program for real work.
- A closed window is not evidence of an uninstall; the installed list is.
