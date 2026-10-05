---
name: update-software
description: Update the operating system or an application, and report what changed
  without pretending a pending reboot is done.
when_to_use: update, upgrade, windows update, patch, latest version, outdated, security
  updates
tools: run_command, system_status, open_app, focus_window, click, press, observe,
  wait_for, notify, ask
version: '1.0'
created: '2026-09-26T21:39:29'
updated: '2026-09-26T21:39:29'
---

# Updating software

## Steps
1. **Say what will update** and confirm it: the whole OS, a specific app, or the
   package set. "Update everything" can restart the machine or sign the user out.
2. **Prefer the terminal.** `run_command` `winget upgrade --all
   --accept-source-agreements --accept-package-agreements` for apps; use the OS
   updater (Settings > Windows Update) for system patches; the distro's package
   manager on Linux.
3. **If a wizard is needed,** `focus_window` it and step through,
   `wait_for`ing each progress window rather than clicking into a frozen one.
4. **Wait for real completion.** Updates download, then install, then ask to
   restart. A pending reboot is not the same as updated - report it as pending
   and let the user choose when to restart.
5. **Report** what was updated, what version it moved to, and whether a restart
   is still outstanding.

## Rules
- Never restart or sign out the machine without the user's go-ahead.
- Do not apply driver or firmware updates unprompted; a bad driver update is far
  harder to undo than a missed patch.
