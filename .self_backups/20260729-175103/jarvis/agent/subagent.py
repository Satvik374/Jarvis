"""Sub-agents, Claude Code style.

The MAIN agent (the desktop loop) can delegate a self-contained sub-task to a
specialist SUB-AGENT via the ``agent`` action. A sub-agent:

  * runs in its own ISOLATED message context (the main loop never sees its
    intermediate steps, only the final report),
  * gets a restricted, headless tool set - never the mouse, keyboard or
    screen, which belong to the main agent alone,
  * has its own persona/system prompt.

Built-ins: ``researcher`` (multi-source web research) and ``coder`` (the
coding engine in coder.py, which reuses the same loop below). Custom agents
are loaded from ``agents/*.yaml`` in the project root - drop in a file with
``name``, ``description``, ``prompt`` and optionally ``actions``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import Config
from ..perception.elements import Observation
from ..tools import registry
from ..tools.schema import ACTIONS
from ..utils import logging as log
from .brain import Brain, complete_with_retry
from .prompts import parse_decision, format_decision, _action_reference

# Actions a headless sub-agent may ever use. Pointer/keyboard/screen actions
# are excluded on purpose: two agents driving one desktop cannot coexist.
HEADLESS = frozenset({
    "read_file", "read_document", "write_file", "write_files", "edit_file",
    "make_dir", "list_dir", "find_files", "copy_file", "move_file",
    "delete_file",
    "run_command", "python", "system_status", "web_search", "read_url",
    "open_url", "http_request", "download_file",
    "clipboard_read", "clipboard_write", "notify", "wait", "finish", "ask",
})


@dataclass(frozen=True)
class AgentSpec:
    name: str
    description: str        # one line, shown to the main agent
    prompt: str             # persona + workflow (the contract is appended)
    actions: frozenset


RESEARCHER = AgentSpec(
    name="researcher",
    description=("deep web research: searches, READS multiple sources, "
                 "cross-checks, returns a cited report"),
    prompt="""\
You are JARVIS-RESEARCHER, a rigorous research analyst. You answer questions
by searching the web and READING sources - never from memory alone.

WORKFLOW
  1. web_search the question (rephrase and search again if results are poor).
  2. read_url the 2-4 most promising results - snippets alone are not enough.
  3. Cross-check: prefer facts confirmed by more than one source; report
     disagreements between sources instead of hiding them.
  4. finish with the complete report: the direct answer first, then key
     facts, then the source URLs you actually read.

RULES
  * Prefer primary/official sources over aggregator blogspam.
  * Quote numbers, dates and names exactly as sourced.
  * If the web gives no reliable answer, say so honestly in finish.""",
    actions=frozenset({"web_search", "read_url", "http_request", "read_file",
                       "read_document", "list_dir", "find_files", "write_file",
                       "wait", "finish", "ask"}),
)


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _load_custom(root: Path) -> dict[str, AgentSpec]:
    out: dict[str, AgentSpec] = {}
    d = root / "agents"
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.yaml")):
        try:
            import yaml  # already a dependency (config.yaml)
            data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            name = str(data.get("name") or f.stem).strip().lower()
            prompt = str(data.get("prompt", "")).strip()
            if not name or not prompt:
                log.warn(f"agent file {f.name}: needs 'name' and 'prompt' - skipped")
                continue
            raw = data.get("actions") or sorted(RESEARCHER.actions)
            acts = (frozenset(str(a).strip() for a in raw) & HEADLESS) | {"finish", "ask"}
            out[name] = AgentSpec(name, str(data.get("description", ""))[:200],
                                  prompt, acts)
        except Exception as exc:
            log.warn(f"could not load agent file {f.name}: {exc}")
    return out


def available(root: Path | None = None) -> dict[str, AgentSpec]:
    """All delegatable sub-agents (built-ins + agents/*.yaml). The coder is
    exposed separately (it needs workdir machinery) but is listed in the note."""
    agents = {"researcher": RESEARCHER}
    agents.update(_load_custom(root or _project_root()))
    return agents


def agents_note() -> str:
    """The SUB-AGENTS section injected into the main agent's system prompt."""
    rows = [f"  * {s.name} - {s.description}" for s in available().values()]
    rows.append("  * coder - professional software engineer that builds and "
                "fixes whole projects (also reachable via code_task)")
    return ("\n\n=== SUB-AGENTS (delegate with the 'agent' action) ===\n"
            + "\n".join(rows)
            + "\n=====================================================")


def _contract(allowed: frozenset) -> str:
    actions = tuple(a for a in ACTIONS if a.name in allowed)
    return (
        "You work in a loop: think, take ONE action, observe its result, "
        "repeat.\nReply with a SINGLE JSON object and nothing else:\n"
        '  {"thought": "<one sentence>", "action": "<name>", "args": {...}}\n\n'
        + _action_reference(actions) + "\n\n"
        "Rules:\n"
        "  * ONE action per turn. Output ONLY the JSON object.\n"
        "  * Your finish summary is the ONLY thing the caller sees - put the\n"
        "    complete final report in it.\n"
        "  * If the task is critically ambiguous, use ask.")


def run_agent(spec: AgentSpec, brain: Brain, cfg: Config, task: str,
              max_steps: int | None = None) -> tuple[str, bool]:
    system = spec.prompt.strip() + "\n\n" + _contract(spec.actions)
    return run_loop(brain, cfg, system, f"TASK: {task}", spec.actions,
                    spec.name, max_steps or max(cfg.safety.max_steps, 40))


def run_loop(brain: Brain, cfg: Config, system: str, initial_user: str,
             allowed: frozenset, label: str, max_steps: int,
             limit_msg: str | None = None) -> tuple[str, bool]:
    """The shared headless agentic loop. Returns ``(message, is_ask)``."""
    # Pointer-free dummy observation: headless handlers never look at it.
    obs = Observation(elements=[], screen_size=(1920, 1080))
    messages: list[dict] = [{"role": "user", "content": initial_user}]
    off_script = 0

    for step_i in range(1, max_steps + 1):
        try:
            with log.spinner(f"{label} (step {step_i}/{max_steps})"):
                raw = complete_with_retry(brain, system, messages)
        except Exception as exc:
            return f"{label} sub-agent brain error: {exc}", False

        d = parse_decision(raw)
        if d.thought:
            log.think(d.thought)

        if d.fallback or d.action not in allowed:
            off_script += 1
            if off_script >= 3:
                return (f"the {label} sub-agent went off-script repeatedly "
                        "and was stopped"), False
            names = ", ".join(sorted(allowed))
            messages.append({"role": "assistant", "content": d.raw[:400]})
            messages.append({"role": "user", "content":
                             "RESULT: invalid reply. Respond with ONE JSON "
                             f"action object using one of: {names}."})
            continue
        off_script = 0

        try:
            result = registry.execute(d.action, d.args, obs, cfg)
        except Exception as exc:
            # Self-healing: feed the crash back so the sub-agent can adapt
            # instead of the whole delegation dying on one bad action.
            result = registry.ActionResult(
                False, f"the {d.action} action crashed with an internal "
                       f"error: {exc}. Try a different approach.")
        log.act(f"{d.action}({_short(d.args)}) -> {_short_msg(result.message)}")
        messages.append({"role": "assistant",
                         "content": format_decision(d.thought, d.action, d.args)})
        messages.append({"role": "user", "content": f"RESULT: {result.message}"})

        if result.finished:
            return result.message, bool(result.ask)

    return (limit_msg or f"the {label} sub-agent hit its {max_steps}-step "
            "limit before finishing"), False


def _short(args: dict) -> str:
    parts = []
    for k, v in (args or {}).items():
        r = repr(v)
        parts.append(f"{k}={r[:57] + '...' if len(r) > 60 else r}")
    return ", ".join(parts)


def _short_msg(msg: str) -> str:
    first = (msg or "").splitlines()[0] if msg else ""
    return first[:117] + "..." if len(first) > 120 else first
