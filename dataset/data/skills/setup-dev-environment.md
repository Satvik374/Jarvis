---
name: setup-dev-environment
description: Get a code project running from a fresh checkout by reading its manifest,
  installing dependencies, and proving it starts.
when_to_use: set up the project, install dependencies, npm install, pip install, requirements,
  virtualenv, venv, clone and run, dev environment, build the project
tools: list_dir, read_file, run_command, python, session_exec, write_file, find_files,
  notify
version: '1.0'
created: '2026-09-26T21:39:29'
updated: '2026-09-26T21:39:29'
---

# Setting up a development environment

## Steps
1. **Read before installing.** `list_dir` the project root and `read_file` its
   manifest(s): `requirements.txt`/`pyproject.toml`, `package.json`,
   `Cargo.toml`, `go.mod`, or the README's setup section. The manifest names the
   dependencies and the run command; guessing does not.
2. **Match the runtime the project expects.** Check the manifest's version pins
   against `python --version` / `node --version`. Do not install a new language
   runtime unless the project needs one and the user agrees.
3. **Install into an isolated environment.** For Python create a virtualenv
   (`python -m venv .venv`) and install into it; for Node run the project's
   package manager and prefer the lockfile (`npm ci` over `npm install`). Keep
   the system environment untouched.
4. **Run the project the way its README says,** then run its tests if it has any.
   A setup that installs cleanly but cannot start is not finished.
5. **Record what worked.** `write_file` a short `SETUP_NOTES.md` (or append to
   the README) with the exact commands, so the next session is a lookup.
6. **Report** the start command and any environment variable still needed.

## Rules
- Prefer lockfile installs over loose upgrades; an unpinned install today is a
  broken build tomorrow.
- If a dependency fails to build, report the real error instead of quietly
  loosening a version pin.
