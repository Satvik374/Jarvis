---
name: install-application
description: Install a program by researching it, preferring a terminal package manager,
  falling back to the browser, and reporting exactly what was done.
when_to_use: install, setup, set up, download and install, get this app, add software,
  winget, package manager, needs installing, how do I get
tools: web_search, read_url, run_command, download_file, open_url, open_app, focus_window,
  click, type, press, wait_for, observe, notify, ask
version: '1.0'
created: '2026-09-26T21:39:29'
updated: '2026-09-26T21:39:29'
---

# Installing an application

## The order that matters
Research first, terminal second, browser third. The browser is a fallback, never
the opening move: a package manager installs the right build, in one step, and
leaves a record you can check afterwards.

## Steps
1. **Research before touching anything.** `web_search` the application by name
   and read the official page with `read_url`. You need: the official source, the
   exact package id or install command, the current version, and any system
   requirement. A download link from an ad or a mirror is how adware gets in.
2. **Try the terminal first.** On Windows use `winget` (then `choco`/`scoop` if
   present); on macOS `brew`; on Linux `apt`/`dnf`/`pacman`. Run it with
   `run_command` - for example
   `winget install --id <Vendor.App> -e --accept-source-agreements`. Prefer a
   package manager over a manual download whenever one carries the program.
3. **If only a manual installer exists, ask before using the browser.** Tell the
   user the package manager does not have it and ask permission to download the
   setup file in the browser. Do not silently switch to clicking.
4. **Browser download, once you have permission.** `open_url` the OFFICIAL
   download page from step 1, find the download control, and let the file arrive.
   Note the filename and folder - it usually lands in Downloads. If the page
   offers a direct link you can trust, `download_file` is faster than clicking.
5. **Run the setup file.** `run_command` the installer path directly (the `.exe`
   or `.msi` you just downloaded). If it needs a wizard, `focus_window` it and
   step through with `click`/`type`/`press`, `wait_for`ing each new window.
   Accept the default choices unless the user asked otherwise, and decline any
   bundled extra software the wizard offers.
6. **Verify it is really installed** - do not assume. Run the program's version
   command with `run_command` (`<app> --version`), or confirm the package with
   `winget list`, before claiming success.
7. **Report back in plain language:** what was installed, which version, where it
   came from, and how to launch it. If it failed, say what failed - never claim a
   success you could not check.

## Rules
- Download from the vendor's own site or an official package repo, never a
  search-result ad, a file-sharing host, or a "crack" site.
- Never disable antivirus or dismiss a security warning to make an installer
  proceed.
- If the installer needs a reboot or admin rights you cannot obtain, stop and
  tell the user exactly what is needed.
