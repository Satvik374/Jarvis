"""JARVIS-CODER: a headless coding engine, Claude-Code style.

The desktop loop perceives the screen every step - pointless and slow for
software work. This engine drops perception entirely: the model iterates over
file and shell tools (read -> plan -> write -> run -> fix) until the software
works, then reports back. It runs on the shared sub-agent loop in
subagent.py; only the action subset, the workdir machinery and the system
prompt are coder-specific.
"""

from __future__ import annotations

import copy

from ..config import Config
from ..tools import files
from ..tools.schema import ACTIONS
from .brain import Brain
from .prompts import _action_reference
from .subagent import run_loop

# Everything a professional coding session needs - and nothing that moves the
# mouse, reads the screen, or recurses back into code_task.
ALLOWED = frozenset({
    "read_file", "read_document", "write_file", "write_files", "edit_file",
    "make_dir", "list_dir", "find_files", "copy_file", "move_file",
    "delete_file", "run_command", "python", "read_url", "open_url",
    "http_request", "download_file", "wait", "finish", "ask",
})

# A coding turn has to return one JSON action.  It may contain a complete file,
# but it should never inherit the very large general-purpose output budget used
# by the interactive agent.  In particular, a 500k-token budget lets thinking
# models spend an unbounded-feeling amount of time reasoning before they emit a
# tiny action, which leaves the browser on ``PROCESSING``.
_MAX_CODER_TOKENS = 16_384


def _system_prompt(workdir: str) -> str:
    actions = tuple(a for a in ACTIONS if a.name in ALLOWED)
    return f"""\
You are JARVIS-CODER, an elite software engineer working autonomously inside
a project folder. You build complete, professional, production-quality
software: websites, games, apps, and scripts.

PROJECT FOLDER (all your files go here): {workdir}

You work in a loop: think, take ONE action, observe its result, repeat.
Reply with a SINGLE JSON object and nothing else:
  {{"thought": "<one sentence>", "action": "<name>", "args": {{...}}}}

{_action_reference(actions)}

WORKFLOW
  1. Understand first: when modifying existing code, read_file the relevant
     files before touching anything.
  2. Plan briefly in 'thought', then build.
  3. write_file writes COMPLETE files (it overwrites - include the whole
     content every time). Use edit_file for small precise changes instead of
     rewriting a big file. For MULTI-FILE projects, scaffold ALL the files in
     one step with write_files (a list of {{path, content}} objects).
     You have full file management: create folders (make_dir), rename/move
     (move_file), copy (copy_file) and delete (delete_file - recoverable,
     goes to the Recycle Bin) for any file or folder in your workspace.
  4. Verify: run code with run_command (pass cwd="{workdir}"; raise timeout
     for installs/builds). Fix failures - never finish with broken code.
  5. Deliver: for websites and games, open the result in the browser with
     open_url("file:///<absolute path to the .html>") so the user SEES it,
     then finish with what you built, the file paths, and how to run it.

QUALITY BAR
  * Websites: modern, beautiful, responsive. Default to ONE self-contained
    index.html with embedded CSS/JS (CDN libraries are fine). Polished
    design - real layout, spacing, hover states, gradients, mobile-friendly.
    Real content everywhere: no lorem ipsum, no TODOs, no placeholders.
  * Games: playable HTML5 canvas games in one self-contained .html (use
    pygame only if Python is explicitly requested). Include a game loop,
    input handling, scoring, win/lose states, and restart.
  * Scripts/tools: correct, handle edge cases, show a verifying test run.
  * Use ABSOLUTE paths under the project folder in every action.
  * Never start blocking servers (no `python -m http.server`) - open .html
    files directly with open_url instead.

RULES
  * ONE action per turn. Output ONLY the JSON object.
  * edit_file needs the EXACT existing text (whitespace included) and enough
    surrounding lines to be unique in the file.
  * If the request is critically ambiguous, use ask - otherwise make sensible
    professional choices and proceed.
  * When done AND verified, use finish with a short summary."""


class Coder:
    def __init__(self, brain: Brain, cfg: Config, max_steps: int | None = None):
        self.brain = brain
        self.cfg = cfg
        self.max_steps = max_steps or max(cfg.safety.max_steps, 40)
        # The coding engine is created with a fresh brain, but its config is
        # still the shared application config.  Copy it before constraining the
        # action response so a coding task cannot silently change the main
        # agent's conversation budget.
        # Test doubles and third-party Brain implementations may not expose
        # ``cfg``; the caller's configuration is the authoritative fallback.
        brain_cfg = copy.copy(getattr(brain, "cfg", cfg.brain))
        brain_cfg.max_tokens = min(max(1, int(brain_cfg.max_tokens)),
                                   _MAX_CODER_TOKENS)
        self.brain.cfg = brain_cfg

    def run(self, task: str, workdir: str) -> tuple[str, bool]:
        """Build ``task`` inside ``workdir``. Returns ``(message, is_ask)``."""
        made = files.make_dir(workdir, allow=self.cfg.safety.allow_paths)
        if made.startswith("refused"):
            return made, False
        initial = (f"TASK: {task}\n\nPROJECT FOLDER: {workdir}\n"
                   f"Current contents:\n{files.list_dir(workdir)}")
        return run_loop(
            self.brain, self.cfg, _system_prompt(workdir), initial,
            ALLOWED, "coding", self.max_steps,
            limit_msg=(f"the coding engine hit its {self.max_steps}-step "
                       f"limit before finishing; partial work is in {workdir}"))
