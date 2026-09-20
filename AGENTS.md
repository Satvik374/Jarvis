# AGENTS.md

Notes for coding agents working in this repo. Terse, non-obvious facts only — the stuff that is not recoverable by reading the code or the README.

## Environment & tooling

- This is **not** the Freebuff tool's project root. The tool's registered workspace root is `C:\Users\Administrator\Downloads\models\lovemefan` (an older SenseVoice project); files written to Jarvis may silently land there instead. Always use absolute paths when creating files; verify new files exist under Jarvis before trusting the tool's success message. (A test once landed in `models/lovemefan/tests/` this way.)
- `python -m pytest` fails at startup with a misleading `PermissionError: [WinError 5] Access is denied ... pytest-of-Administrator\pytest-current` — a corrupted temp-dir symlink, not a test failure. Workaround: add `--basetemp="$TEMP/jv_basetemp"`.
- Full suite: `python -m pytest -q -p no:cacheprovider --basetemp=...` → ~1115 tests, ~80 s. Keep it green; it is the regression gate for tool-layer changes.
- Repo uses CRLF line endings — preserve them when editing Python files; don't let edits introduce bare-LF lines.
- Source files live here, **not** in the many `.self_backups/<timestamp>/` snapshot trees at the repo root (an app self-upgrade mechanism). Never edit or grep-check those copies by mistake.

## Architecture & seams

- `jarvis/tools/schema.py` declares all 89 actions (dependency-free — the dataset builder and training pipeline import it, so it must not import desktop modules). `jarvis/tools/registry.py` implements them as `_h_<name>` handlers, and the `_HANDLERS` dispatch dict is **computed** by `_bind_handlers()` from the declarations via the `_h_<name>` naming convention. The name is now written only twice (declaration + handler); `tests/test_action_space.py` (19 tests) is the gate that keeps both halves in agreement — run it after touching either file.
- Schema and registry must change **together**: declaring an action without a `_h_<name>` handler (or vice versa) is reported by a `log.warn` at import, but the hard failure is the test file.
- `execute()` must keep raising `UnknownAction` for unbound names — callers like `jarvis/live/direct_tools.py` catch it and map it to a tool error; don't convert the binder to raise at import time.
- A second, deliberate action space exists in `jarvis/live/screen_tools.py` (5 voice-path helpers) with its own private `DECLARATIONS` dict — same job as `schema.py`, different vocabulary. It is intentionally outside `schema.py`; two live transports derive their wire formats from it. Don't "fix" it without deciding the convergence question first.
- Handlers do **not** read their declared `Param`s; argument coercion is hand-rolled per handler (~206 call sites). Defaults/clamps live in handlers only — the declared schema under-specifies runtime behaviour (e.g. `scroll` clamps dy/dx to ±50, undeclared). Changing any of this alters the model-visible schema — treat as behaviour change, verify with the action-space snapshot harness in `%TEMP%/jarvis_*.json` before/after.
- Only two actions are terminal loop-enders: `finish` and `ask` (asserted in `tests/test_action_space.py`).

## Working tree & process

- Multiple agent threads work in this same checkout concurrently. Expect uncommitted changes not made by you (e.g. `agent/brain.py`, `live/client.py`, `live/readiness.py`, several test files, untracked `_*.py` probe scripts at the root); leave them alone and stage only your own files.
- Verify claims by re-running the measurement, not from session memory — counts drift as other threads edit.
