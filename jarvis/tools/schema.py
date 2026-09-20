"""Single source of truth for Jarvis's action space.

Every layer of the system references this file so they can never drift apart:

  * the runtime agentic loop (``jarvis.agent.loop``) executes these actions,
  * the system prompt (``jarvis.agent.prompts``) documents them to the model,
  * the dataset builder (``dataset/build_dataset.py``) generates training
    examples that emit exactly these actions,
  * the training pipeline (``training/``) fine-tunes a model to produce them.

An *action* is the atomic unit the brain emits each step. The model always
replies with a single JSON object of the form::

    {"thought": "<short reasoning>", "action": "<name>", "args": {...}}

Keeping this module free of heavy dependencies (no pyautogui / torch / uiautomation)
is deliberate: the dataset builder and tests can import it anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Param:
    name: str
    type: str
    description: str
    required: bool = True
    default: Any = None
    # Optional numeric bounds. Declared here means the model sees them in the
    # JSON schema (``minimum`` / ``maximum``) and the handler is expected to
    # enforce exactly this range - one contract, visible to every consumer.
    minimum: float | None = None
    maximum: float | None = None


@dataclass(frozen=True)
class Action:
    name: str
    summary: str
    params: tuple[Param, ...] = field(default_factory=tuple)
    # ``category`` groups actions in the docs; ``terminal`` marks actions that
    # end the loop (``finish`` / ``ask``).
    category: str = "control"
    terminal: bool = False
    # A couple of realistic example arg dicts, reused as few-shot seeds.
    examples: tuple[dict, ...] = field(default_factory=tuple)


# A pointer target may be given either as an element id from the current
# perception snapshot (preferred, accurate) or as raw x/y pixel coordinates.
_TARGET_PARAMS = (
    Param("element", "int", "Id of a labelled element from the current screen "
          "observation. Prefer this over raw coordinates.", required=False),
    Param("x", "int", "Absolute screen x pixel. Use only when no element id fits.",
          required=False),
    Param("y", "int", "Absolute screen y pixel. Use only when no element id fits.",
          required=False),
)


ACTIONS: tuple[Action, ...] = (
    # ---- pointer ---------------------------------------------------------
    Action(
        "click", "Left-click an element or screen coordinate. Pass 'count' to "
        "click several times in place (e.g. count 2 = double, 3 = triple).",
        _TARGET_PARAMS + (
            Param("count", "int", "How many clicks (1-10, default 1).",
                  required=False, default=1, minimum=1, maximum=10),
        ), category="pointer",
        examples=({"element": 4}, {"x": 640, "y": 360}, {"element": 4, "count": 3}),
    ),
    Action(
        "double_click", "Double-click an element or coordinate (open items, "
        "select a word).", _TARGET_PARAMS, category="pointer",
        examples=({"element": 7},),
    ),
    Action(
        "triple_click", "Triple-click an element or coordinate (selects a whole "
        "line or paragraph).", _TARGET_PARAMS, category="pointer",
        examples=({"element": 5},),
    ),
    Action(
        "right_click", "Right-click to open a context menu.",
        _TARGET_PARAMS, category="pointer",
        examples=({"element": 2},),
    ),
    Action(
        "move", "Move the mouse without clicking (hover to reveal menus).",
        (Param("x", "int", "Absolute screen x."),
         Param("y", "int", "Absolute screen y.")),
        category="pointer", examples=({"x": 100, "y": 200},),
    ),
    Action(
        "drag", "Press at one point/element and release at another.",
        (Param("from_element", "int", "Source element id.", required=False),
         Param("x1", "int", "Source x.", required=False),
         Param("y1", "int", "Source y.", required=False),
         Param("to_element", "int", "Target element id.", required=False),
         Param("x2", "int", "Target x.", required=False),
         Param("y2", "int", "Target y.", required=False)),
        category="pointer",
        examples=({"x1": 300, "y1": 400, "x2": 600, "y2": 400},),
    ),
    Action(
        "scroll", "Scroll the active window. Positive dy scrolls down.",
        (Param("dy", "int", "Vertical clicks; positive = down, negative = up "
               "(clamped to -50..50, the safe range for the OS scroll call).",
               required=False, default=3, minimum=-50, maximum=50),
         Param("dx", "int", "Horizontal clicks; positive = right "
               "(clamped to -50..50).",
               required=False, default=0, minimum=-50, maximum=50)),
        category="pointer", examples=({"dy": 5}, {"dy": -3}),
    ),
    Action(
        "mouse_control", "Turn camera hand control on or off. While on, the "
        "index fingertip moves the mouse and touching thumb to index finger "
        "once produces one left-click. Touch index and middle fingertips "
        "together and move them up/down to scroll up/down. Hold thumb, index, "
        "and middle fingers open with ring and pinky closed, then move the "
        "hand right/left to increase/decrease system volume. Always turn it off when the user "
        "asks to stop hand/mouse camera control.",
        (Param("enabled", "bool", "True to start hand control; false to stop."),
         Param("camera", "int", "Camera index 0-9 (default 0).",
               required=False, default=0, minimum=0, maximum=9)),
        category="pointer",
        examples=({"enabled": True}, {"enabled": False},
                  {"enabled": True, "camera": 1}),
    ),
    # ---- keyboard --------------------------------------------------------
    Action(
        "type", "Type literal text at the current keyboard focus.",
        (Param("text", "str", "The exact text to type."),),
        category="keyboard", examples=({"text": "hello world"},),
    ),
    Action(
        "press", "Press a single key or ANY hotkey combo. Join keys with '+'. "
        "Works for letters, digits, function keys (f1-f12), arrows (up/down/"
        "left/right), and modifiers (ctrl/shift/alt/win) in any combination - "
        "e.g. 'enter', 'ctrl+s', 'ctrl+enter', 'shift+j', 'ctrl+k', "
        "'ctrl+shift+p', 'alt+tab', 'ctrl+alt+delete'.",
        (Param("keys", "str", "Key name or '+'-joined combo."),),
        category="keyboard",
        examples=({"keys": "enter"}, {"keys": "ctrl+s"}, {"keys": "ctrl+enter"},
                  {"keys": "shift+j"}, {"keys": "ctrl+k"}, {"keys": "alt+tab"}),
    ),
    Action(
        "key_sequence", "Press several keys/combos one after another in a single "
        "step. Give an ordered list; each item is a key or '+'-combo.",
        (Param("keys", "list", "Ordered list of keys/combos to press in turn."),),
        category="keyboard",
        examples=({"keys": ["ctrl+a", "ctrl+c"]},
                  {"keys": ["down", "down", "enter"]}),
    ),
    # ---- apps / os -------------------------------------------------------
    Action(
        "open_app", "Launch or focus an application by name (e.g. 'notepad', "
        "'chrome', 'calculator', 'explorer').",
        (Param("name", "str", "Application name or executable."),),
        category="apps", examples=({"name": "notepad"}, {"name": "chrome"}),
    ),
    Action(
        "open_url", "Open a URL in the default web browser.",
        (Param("url", "str", "Fully-qualified URL."),),
        category="apps", examples=({"url": "https://www.google.com"},),
    ),
    Action(
        "read_url", "Fetch a web page and return its readable text WITHOUT "
        "opening a browser. Prefer this over open_url whenever you only need "
        "to read, research or summarise a page's content.",
        (Param("url", "str", "Fully-qualified URL to fetch."),
         Param("max_chars", "int", "Max characters of text to return "
               "(default 8000).", required=False, default=8000)),
        category="apps",
        examples=({"url": "https://en.wikipedia.org/wiki/Python_(programming_language)"},),
    ),
    Action(
        "browser_action", "Direct high-speed browser automation (Playwright / CDP). "
        "Interact directly with web pages: navigate, click buttons/links, fill forms, "
        "select options, scroll, evaluate JS, extract structured markdown/text, "
        "and inspect interactive element tags ([e1], [e2]). Automatically captures "
        "instant clean screenshots for visual verification without mouse interference.",
        (Param("action", "str", "The browser action: 'navigate', 'click', 'type', "
               "'select', 'scroll', 'hover', 'press', 'extract', 'snapshot', "
               "'screenshot', 'eval', 'close'."),
         Param("url", "str", "Target URL (for 'navigate').", required=False),
         Param("target", "str", "Target element index (e.g. 'e1', 'e2'), CSS selector, "
               "or text (for 'click', 'type', 'select', 'hover').", required=False),
         Param("text", "str", "Text to type (for 'type'), JS expression (for 'eval'), "
               "or key name (for 'press', e.g. 'Enter').", required=False),
         Param("value", "str", "Value to select (for 'select').", required=False),
         Param("direction", "str", "Scroll direction: 'down', 'up', 'top', 'bottom'.", required=False, default="down"),
         Param("mode", "str", "Extraction mode: 'markdown', 'text', 'html'.", required=False, default="markdown"),
         Param("headless", "bool", "Optional override to run browser headfully or headlessly.", required=False),
         Param("press_enter", "bool", "Press Enter after typing (for 'type'; default false).", required=False, default=False),
         Param("amount", "int", "Scroll distance in pixels for 'scroll' (default 500).", required=False, default=500),
         Param("path", "str", "Where to save for action='screenshot' (default: a timestamped file).", required=False),
         Param("script", "str", "JavaScript to run for action='eval' (alias of 'text').", required=False)),
        category="apps",
        examples=(
            {"action": "navigate", "url": "https://news.ycombinator.com"},
            {"action": "click", "target": "e1"},
            {"action": "type", "target": "input[name=q]", "text": "Jarvis AI"},
            {"action": "extract", "mode": "markdown"},
            {"action": "snapshot"},
        ),
    ),
    Action(
        "http_request", "Call any web API and get the raw response back. "
        "Unlike read_url (which only reads a page as text), this speaks to "
        "JSON/REST APIs: choose the method, send headers (e.g. an auth token), "
        "query params and a JSON or form body. Use it for webhooks, cloud "
        "services, smart-home endpoints - anything with an API.",
        (Param("url", "str", "Full URL of the endpoint."),
         Param("method", "str", "HTTP method: GET/POST/PUT/PATCH/DELETE "
               "(default GET).", required=False, default="GET"),
         Param("headers", "dict", "Request headers as an object, e.g. "
               "{'Authorization': 'Bearer ...'}.", required=False),
         Param("params", "dict", "URL query parameters as an object.",
               required=False),
         Param("json_body", "dict", "A JSON request body (sets the "
               "Content-Type automatically).", required=False),
         Param("data", "str", "A raw or form request body (use instead of "
               "json_body).", required=False),
         Param("timeout", "int", "Max seconds to wait (default 30).",
               required=False, default=30)),
        category="apps",
        examples=({"url": "https://api.github.com/repos/python/cpython"},
                  {"url": "https://api.example.com/v1/items", "method": "POST",
                   "headers": {"Authorization": "Bearer TOKEN"},
                   "json_body": {"name": "widget", "qty": 3}}),
    ),
    Action(
        "list_windows", "List the titles of every open window. Use it to find "
        "the exact title before focus_window or close_window.", (),
        category="apps", examples=({},),
    ),
    Action(
        "close_window", "Close the first open window whose title contains the "
        "given text (graceful close, like clicking the X button).",
        (Param("title", "str", "Case-insensitive substring of the window title."),),
        category="apps", examples=({"title": "Notepad"},),
    ),
    Action(
        "snap_window", "Position and resize a window to a region of the screen: "
        "'left' (left half), 'right' (right half), 'top' (top half), 'bottom' (bottom half), "
        "'maximize', 'minimize', 'restore', or 'center'. Snaps active window by default, or pass 'title'.",
        (Param("direction", "str", "Snap direction: left, right, top, bottom, maximize, minimize, restore, center."),
         Param("title", "str", "Optional window title substring. If omitted, snaps active foreground window.", required=False)),
        category="apps",
        examples=({"direction": "left"}, {"direction": "maximize", "title": "Chrome"}),
    ),
    Action(
        "tile_windows", "Automatically arrange open desktop windows into a clean workspace layout: "
        "'side_by_side' (columns), 'grid' (2x2 grid), or 'minimize_all'.",
        (Param("layout", "str", "Layout pattern: side_by_side, grid, minimize_all (default: side_by_side).", required=False, default="side_by_side"),),
        category="apps",
        examples=({"layout": "side_by_side"}, {"layout": "grid"}),
    ),
    Action(
        "run_command", "Run a shell command and capture its output. Use for "
        "non-GUI tasks. Refuses obviously destructive commands.",
        (Param("command", "str", "The command line to execute."),
         Param("cwd", "str", "Working directory to run in.", required=False),
         Param("timeout", "int", "Max seconds to wait (default 60, max 600 - "
               "raise it for installs/builds).", required=False, default=60)),
        category="system",
        examples=({"command": "ipconfig"},
                  {"command": "python main.py", "cwd": "~/JarvisProjects/app"}),
    ),
    Action(
        "python", "Run a Python snippet and get its output. A general compute "
        "tool: use any installed library, crunch data, do maths, parse text, "
        "generate content - without the shell-quoting pain of run_command. "
        "print() whatever you want to see; it runs isolated in its own process "
        "with a timeout, so a crash or infinite loop can't take you down.",
        (Param("code", "str", "The Python source to execute."),
         Param("timeout", "int", "Max seconds to run (default 60, max 600 - "
               "raise it for heavy work).", required=False, default=60),
         Param("cwd", "str", "Working directory to run in.", required=False)),
        category="system",
        examples=({"code": "import statistics; print(statistics.mean([2,4,9]))"},
                  {"code": "print(sum(1 for _ in open(r'C:/data/log.txt')))"}),
    ),
    Action(
        "synthesize_tool", "Synthesize, validate, test, and permanently register a new reusable Python tool or script. "
        "The tool is saved to disk under tools_synthesized/<name>.py, indexed into persistent Long-Term Memory / Knowledge Graph, "
        "and immediately made available for execution via execute_synthesized_tool across all future sessions.",
        (Param("name", "str", "Unique snake_case identifier name for the tool, e.g. 'batch_image_resizer', 'csv_cleaner'."),
         Param("description", "str", "Clear description of the task, inputs, and what the tool accomplishes."),
         Param("code", "str", "Complete, self-contained Python script implementing the tool. Can define a run(**kwargs) function or main()."),
         Param("parameters", "dict", "Optional dictionary describing expected parameters, e.g. {'folder': {'type': 'str', 'description': 'path to folder'}}.", required=False),
         Param("tags", "list", "Optional list of searchable keywords/tags.", required=False),
         Param("test_args", "dict", "Optional sample arguments to execute a test run and verify functionality before saving.", required=False)),
        category="coding",
        examples=({
            "name": "csv_to_markdown_table",
            "description": "Convert any CSV file into a cleanly formatted Markdown table.",
            "code": "import csv, io\ndef run(csv_path):\n    with open(csv_path, 'r', encoding='utf-8') as f:\n        rows = list(csv.reader(f))\n    if not rows: return ''\n    headers = rows[0]\n    lines = ['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(['---'] * len(headers)) + ' |']\n    for r in rows[1:]:\n        lines.append('| ' + ' | '.join(r) + ' |')\n    return '\\n'.join(lines)\n",
            "parameters": {"csv_path": {"type": "str", "description": "Path to CSV file"}},
            "tags": ["csv", "markdown", "table", "converter"]
        },),
    ),
    Action(
        "execute_synthesized_tool", "Execute a previously synthesized and remembered Python tool or script by name. "
        "Runs in a sandboxed subprocess with parameters passed as JSON, capturing stdout, stderr, and return values.",
        (Param("name", "str", "Name of the synthesized tool to execute."),
         Param("args", "dict", "Dictionary of arguments to pass to the tool.", required=False),
         Param("timeout", "int", "Max seconds to run (default 60).", required=False, default=60),
         Param("cwd", "str", "Optional working directory.", required=False)),
        category="coding",
        examples=({
            "name": "csv_to_markdown_table",
            "args": {"csv_path": "data.csv"}
        },),
    ),
    Action(
        "list_synthesized_tools", "List, search, and inspect all dynamic tools and scripts that Jarvis has synthesized and remembered. "
        "Allows looking up available custom tools, their parameters, and file locations.",
        (Param("query", "str", "Optional search keyword or task description to find matching tools.", required=False),),
        category="coding",
        examples=({"query": "csv"}, {"query": "image resize"}),
    ),
    Action(
        "session_exec", "Execute commands in a persistent stateful interactive session "
        "(PowerShell, CMD, Bash, Python). Unlike run_command, variables, virtual environments, "
        "directory changes, and REPL state persist across turns. "
        "Operations: 'exec' (run command in named session), 'start' (launch named session), "
        "'list' (show active sessions), 'close' (terminate session).",
        (Param("command", "str", "Command or expression to run (for op='exec').", required=False),
         Param("op", "str", "Operation: 'exec', 'start', 'list', or 'close' (default 'exec').", required=False, default="exec"),
         Param("name", "str", "Session identifier name, e.g. 'dev', 'build', 'py' (default 'default').", required=False, default="default"),
         Param("shell_type", "str", "Shell type for start: 'powershell', 'cmd', 'bash', 'python' (default 'powershell').", required=False, default="powershell"),
         Param("cwd", "str", "Working directory to start the session in (for op='start').", required=False),
         Param("timeout", "int", "Max seconds to wait for command output (default 30).", required=False, default=30)),
        category="system",
        examples=({"command": "$x = 42", "op": "exec"},
                  {"command": "Write-Output $x", "op": "exec"},
                  {"op": "start", "name": "py", "shell_type": "python"},
                  {"op": "list"},
                  {"op": "close", "name": "dev"}),
    ),
    Action(
        "agent", "Delegate a self-contained sub-task to a specialist "
        "sub-agent (see the SUB-AGENTS list in the system prompt). It works "
        "in its own isolated context with its own tools and returns only its "
        "final report. Use it for deep web research, document writing, or any "
        "big sub-task, so this loop stays fast and focused.",
        (Param("name", "str", "The sub-agent to run, e.g. 'researcher', "
               "'verifier', 'data_analyst', 'architect', 'coder'."),
         Param("task", "str", "Complete, self-contained instructions - the "
               "sub-agent cannot see this conversation.")),
        category="coding",
        examples=({"name": "researcher",
                   "task": "Find the current stable Python version and its "
                           "release date; cite the sources you read"},),
    ),
    Action(
        "agent_swarm", "Delegate multiple specialist sub-agent tasks concurrently "
        "in parallel (e.g. running research, data analysis, architectural design, "
        "and code verification simultaneously). Each sub-agent runs independently "
        "in its own worker thread with isolated context, returning an aggregated report.",
        (Param("tasks", "list", "List of sub-task objects, e.g. "
               "[{'name': 'researcher', 'task': '...'}, {'name': 'verifier', 'task': '...'}]."),
         Param("timeout", "int", "Max seconds to wait for swarm completion (default 180).",
               required=False, default=180)),
        category="coding",
        examples=({
            "tasks": [
                {"name": "researcher", "task": "Search for best practices on SQLite WAL mode performance"},
                {"name": "verifier", "task": "Run pytest on the database module and check for concurrency issues"},
            ],
            "timeout": 120,
        },),
    ),
    Action(
        "code_task", "Delegate a complete software-development job to the "
        "specialist coding engine: it plans, writes files, edits code, runs "
        "commands and iterates until the software works - far faster and "
        "better at code than this loop. Use it ONLY for software development "
        "(websites, web apps, games, scripts, refactors, debugging code) - "
        "never for ordinary desktop, file or settings tasks.",
        (Param("description", "str", "Full requirements: what to build, "
               "features, style, tech preferences."),
         Param("workdir", "str", "Project folder; when omitted a new folder "
               "under ~/JarvisProjects is created (default '').", required=False, default="")),
        category="coding",
        examples=({"description": "Build a modern portfolio website with a "
                   "dark theme, hero section, projects grid and contact form"},
                  {"description": "Create a playable Snake game in HTML5 "
                   "canvas with score and restart",
                   "workdir": "~/JarvisProjects/snake"}),
    ),
    Action(
        "code_intel", "Analyze codebases using AST parsing, symbol extraction, "
        "cross-project symbol search, dependency mapping, or architectural tree summaries. "
        "Operations: 'symbols' (classes/functions/methods/types/docstrings in a file/folder), "
        "'search' (search for symbol definitions across codebase), 'dependencies' (third-party and internal imports), "
        "'summary' (structural tree overview with symbol counts).",
        (Param("op", "str", "Operation: 'symbols', 'search', 'dependencies', or 'summary' (default 'symbols').", required=False, default="symbols"),
         Param("path", "str", "File or folder path to analyze (default: current directory).", required=False, default="."),
         Param("query", "str", "Symbol name to search for (used with op='search').", required=False),
         Param("max_depth", "int", "Maximum folder depth for summary tree (default 3).", required=False, default=3)),
        category="coding",
        examples=({"op": "symbols", "path": "jarvis/agent/loop.py"},
                  {"op": "search", "query": "Brain", "path": "jarvis"},
                  {"op": "dependencies", "path": "."},
                  {"op": "summary", "path": "jarvis"}),
    ),
    Action(
        "db_query", "Inspect database schema, list tables, analyze query plans, "
        "and execute SQL statements (SQLite, and relational databases). "
        "Operations: 'query' (run SQL and format output as table/json/csv), "
        "'schema' (introspect tables, columns, types, foreign keys, indexes), "
        "'tables' (list all tables with row counts), 'explain' (explain query execution plan).",
        (Param("sql", "str", "SQL query or DDL statement to execute.", required=False),
         Param("path", "str", "Database file path or connection string (default: Jarvis memory database).", required=False, default=""),
         Param("op", "str", "Operation: 'query', 'schema', 'tables', or 'explain' (default 'query').", required=False, default="query"),
         Param("limit", "int", "Maximum rows to return for SELECT queries (default 100).", required=False, default=100),
         Param("output_format", "str", "Result format: 'table', 'json', or 'csv' (default 'table').", required=False, default="table"),
         Param("params", "list", "Positional bind parameters for '?' placeholders in sql.", required=False)),
        category="coding",
        examples=({"op": "schema", "path": "data/app.db"},
                  {"sql": "SELECT * FROM users WHERE active = 1", "path": "app.db", "output_format": "json"},
                  {"op": "tables", "path": "app.db"},
                  {"sql": "EXPLAIN QUERY PLAN SELECT * FROM logs", "op": "explain", "path": "app.db"}),
    ),
    Action(
        "git_intel", "Inspect and operate on Git repositories. "
        "Operations: 'status' (enumerate branch, staged, unstaged, untracked files), "
        "'log' (structured commit history), 'diff' (unified diff of staged/unstaged changes), "
        "'branches' (list local and remote branches), 'commit' (stage files and create commit).",
        (Param("op", "str", "Operation: 'status', 'log', 'diff', 'branches', or 'commit' (default 'status').", required=False, default="status"),
         Param("path", "str", "Repository path (default: current directory).", required=False, default="."),
         Param("target", "str", "Pathspec or file to stage for op='commit' (default: all changes).", required=False),
         Param("message", "str", "Commit message for op='commit'.", required=False),
         Param("limit", "int", "Maximum number of commits for op='log' (default 10).", required=False, default=10),
         Param("staged", "bool", "Show staged changes diff for op='diff' (default False).", required=False, default=False)),
        category="coding",
        examples=({"op": "status"},
                  {"op": "log", "limit": 5},
                  {"op": "diff"},
                  {"op": "commit", "message": "feat: add new feature"}),
    ),
    Action(
        "self_upgrade", "Modify JARVIS'S OWN source code - upgrade one of your "
        "capabilities or fix a bug in yourself. Use ONLY when the user "
        "explicitly asks you to upgrade/improve/fix yourself, or to repair a "
        "recurring internal Jarvis error. Your source is snapshotted first and "
        "restored automatically if the changed code fails verification; "
        "changes take effect on the next restart.",
        (Param("description", "str", "Exactly what to change, add or fix in "
               "Jarvis's own code, and why."),),
        category="coding",
        examples=({"description": "Add a 'zoom' action that presses "
                   "ctrl+plus/ctrl+minus, registered in schema.py and "
                   "registry.py"},),
    ),
    Action(
        "self_heal", "Execute an automated self-healing recovery intervention when "
        "an application loses focus, becomes unresponsive, or an action gets stuck. "
        "Strategies: 'refocus' (brings target window to front), 'escape' (clears modal popups/menus), "
        "'restart_app' (kills and relaunches a stuck app), 'reset_state' (resets keyboard/mouse modifiers).",
        (Param("strategy", "str", "Recovery strategy: 'refocus', 'escape', 'restart_app', or 'reset_state'."),
         Param("target", "str", "Target window title or executable name (e.g. 'Notepad' or 'notepad.exe').", required=False)),
        category="system",
        examples=({"strategy": "refocus", "target": "Notepad"},
                  {"strategy": "escape"},
                  {"strategy": "restart_app", "target": "notepad.exe"}),
    ),
    Action(
        "daemon_rule", "Configure proactive background daemon event triggers and rules. "
        "Allows Jarvis to automatically respond to hardware/OS events (low battery, high CPU/RAM, new downloads, morning routines). "
        "Supported actions: 'list' (view rules), 'add' (create rule), 'remove' (delete rule), 'enable', 'disable', 'status'.",
        (Param("action", "str", "Operation: 'list', 'add', 'remove', 'enable', 'disable', or 'status'."),
         Param("rule_id", "str", "Rule ID (for remove/enable/disable).", required=False),
         Param("name", "str", "Human readable rule name (for add).", required=False),
         Param("trigger", "str", "Trigger event: 'battery_low', 'battery_charging', 'high_cpu', 'high_memory', 'file_dropped', 'morning_routine', 'app_launched'.", required=False),
         Param("action_type", "str", "Action type: 'notify' (voice/UI alert), 'task' (run agent prompt), or 'macro' (replay macro).", required=False, default="notify"),
         Param("target", "str", "Action target (notification text, task prompt, or macro name).", required=False),
         Param("cooldown", "int", "Cooldown in seconds between triggers (default 300).", required=False, default=300)),
        category="system",
        examples=({"action": "list"},
                  {"action": "add", "name": "Download Alert", "trigger": "file_dropped", "action_type": "notify", "target": "New file arrived in downloads."},
                  {"action": "enable", "rule_id": "default_battery_low"}),
    ),
    Action(
        "hud_control", "Control the Global Floating Mini HUD overlay and system state display. "
        "Allows showing, hiding, toggling, or updating the state/detail text on the always-on-top HUD capsule. "
        "Operations: 'show', 'hide', 'toggle', 'set_state', 'status'.",
        (Param("action", "str", "Operation: 'show', 'hide', 'toggle', 'set_state', or 'status'."),
         Param("state", "str", "State name: 'idle', 'listening', 'thinking', 'acting', 'speaking', 'healing', 'success', 'error'.", required=False),
         Param("detail", "str", "Optional short detail/toast text to display on the HUD.", required=False)),
        category="system",
        examples=({"action": "show"},
                  {"action": "set_state", "state": "acting", "detail": "Executing user task"},
                  {"action": "toggle"}),
    ),
    Action(
        "focus_window", "Bring an open window matching a title substring to the "
        "foreground.",
        (Param("title", "str", "Case-insensitive substring of the window title."),),
        category="apps", examples=({"title": "Notepad"},),
    ),
    # ---- files -----------------------------------------------------------
    Action(
        "read_file", "Read a UTF-8 text file and return its content.",
        (Param("path", "str", "Absolute or user-relative file path."),),
        category="files", examples=({"path": "~/notes.txt"},),
    ),
    Action(
        "read_document", "Read the text out of a document - PDF, Word (.docx), "
        "Excel (.xlsx), PowerPoint (.pptx) or CSV - and return it. Use this "
        "instead of read_file for anything that is not plain text, e.g. to "
        "summarise a PDF or see what is in a spreadsheet.",
        (Param("path", "str", "Path to the document."),
         Param("max_chars", "int", "Max characters to return (default 20000).",
               required=False, default=20000)),
        category="files",
        examples=({"path": "~/Downloads/report.pdf"},
                  {"path": "~/Documents/budget.xlsx"}),
    ),
    Action(
        "write_file", "Create or overwrite a UTF-8 text file (use for code too).",
        (Param("path", "str", "Destination path; parent folders are auto-created."),
         Param("content", "str", "Full file content.")),
        category="files",
        examples=({"path": "~/todo.txt", "content": "buy milk"},
                  {"path": "~/projects/app/main.py",
                   "content": "print('hello world')\n"}),
    ),
    Action(
        "write_files", "Create or overwrite SEVERAL text files in ONE step. "
        "Give a list of {path, content} objects. Use this to scaffold "
        "multi-file projects instead of one write_file call per file.",
        (Param("files", "list", "List of objects, each with 'path' and "
               "'content' keys."),),
        category="files",
        examples=({"files": [
            {"path": "~/projects/app/index.html",
             "content": "<!doctype html>..."},
            {"path": "~/projects/app/app.js",
             "content": "console.log('hi');"}]},),
    ),
    Action(
        "edit_file", "Surgically edit a text file: replace ONE exact "
        "occurrence of 'old' with 'new'. Fails if the text is missing or "
        "matches more than once (add surrounding lines to make it unique).",
        (Param("path", "str", "File to edit."),
         Param("old", "str", "The exact existing text (with whitespace)."),
         Param("new", "str", "The replacement text.")),
        category="files",
        examples=({"path": "~/projects/app/main.py",
                   "old": "DEBUG = True", "new": "DEBUG = False"},),
    ),
    Action(
        "make_dir", "Create a folder (and any missing parent folders).",
        (Param("path", "str", "Folder path to create."),),
        category="files",
        examples=({"path": "~/projects/myapp"},
                  {"path": "~/projects/myapp/src"}),
    ),
    Action(
        "list_dir", "List the entries of a directory.",
        (Param("path", "str", "Directory path.", required=False, default="."),),
        category="files", examples=({"path": "~/Downloads"},),
    ),
    Action(
        "find_files", "Search for files or folders by name under a folder "
        "(recursive). A plain word matches as a substring; * and ? wildcards "
        "also work.",
        (Param("pattern", "str", "Name or wildcard pattern to search for."),
         Param("root", "str", "Folder to search under (default: home).",
               required=False, default="~"),
         Param("max_results", "int", "Stop after this many matches "
               "(default 40).", required=False, default=40)),
        category="files",
        examples=({"pattern": "resume"},
                  {"pattern": "*.pdf", "root": "~/Downloads"}),
    ),
    Action(
        "download_file", "Download a file from a URL to disk (streamed, "
        "size-capped). For installers, datasets, images, PDFs, archives - "
        "anything. dest may be a folder or a full path; defaults to "
        "~/Downloads. Aborts if the file exceeds max_mb.",
        (Param("url", "str", "URL of the file to download."),
         Param("dest", "str", "Destination folder or file path "
               "(default: ~/Downloads).", required=False),
         Param("max_mb", "int", "Abort if the file exceeds this many MB "
               "(default 500).", required=False, default=500)),
        category="files",
        examples=({"url": "https://example.com/data.csv"},
                  {"url": "https://example.com/app.zip",
                   "dest": "~/Downloads/app.zip"}),
    ),
    Action(
        "copy_file", "Copy a file or folder. If dst is an existing folder the "
        "item is copied into it. Never overwrites.",
        (Param("src", "str", "Source path."),
         Param("dst", "str", "Destination path or folder.")),
        category="files",
        examples=({"src": "~/Downloads/report.pdf", "dst": "~/Documents"},),
    ),
    Action(
        "move_file", "Move or rename a file or folder. If dst is an existing "
        "folder the item is moved into it. Never overwrites.",
        (Param("src", "str", "Source path."),
         Param("dst", "str", "Destination path or folder.")),
        category="files",
        examples=({"src": "~/Downloads/report.pdf", "dst": "~/Documents"},
                  {"src": "~/notes.txt", "dst": "~/notes_old.txt"}),
    ),
    Action(
        "delete_file", "Send a file or folder to the Recycle Bin (recoverable "
        "- never a permanent delete).",
        (Param("path", "str", "File or folder to delete."),),
        category="files", examples=({"path": "~/Downloads/old_setup.exe"},),
    ),
    Action(
        "convert_file", "Convert and transform files across formats: "
        "Markdown -> styled HTML, CSV <-> JSON <-> YAML, CSV <-> XLSX (Excel), "
        "Images (PNG/JPEG/WEBP/BMP/GIF/ICO with resize/quality options), "
        "Base64 encode/decode, and UTF-8 / line ending normalization. "
        "Pass 'target' (output path) and/or 'target_format' (e.g. 'html', 'json', 'csv', 'yaml', 'xlsx', 'webp', 'png', 'base64').",
        (Param("source", "str", "Path to the source file to convert."),
         Param("target", "str", "Path where converted file should be saved.", required=False),
         Param("target_format", "str", "Target format: 'html', 'json', 'csv', 'yaml', 'xlsx', 'png', 'jpg', 'webp', 'bmp', 'base64', 'utf8', 'crlf', 'lf'.", required=False),
         Param("options", "dict", "Optional conversion options: {'theme': 'dark'|'light'|'cyber', 'title': '...', 'resize_width': int, 'resize_height': int, 'quality': int, 'delimiter': str, 'auto_types': bool}.", required=False)),
        category="files",
        examples=({"source": "~/notes.md", "target": "~/notes.html", "options": {"theme": "dark"}},
                  {"source": "~/data.csv", "target_format": "json"},
                  {"source": "~/photo.png", "target": "~/photo.webp", "options": {"resize_width": 800, "quality": 85}},
                  {"source": "~/data.csv", "target": "~/data.xlsx"}),
    ),
    Action(
        "archive_intel", "Inspect, test, pack, and safely extract archives (ZIP, TAR, TAR.GZ, TAR.BZ2, TAR.XZ). "
        "Built-in Zip-Slip path-traversal protection. "
        "Operations: 'list' (view entries, sizes, compression savings), 'test' (verify integrity and checksums), "
        "'create' (pack directory/files into archive), 'extract' (safely unpack archive to destination).",
        (Param("path", "str", "Path to archive or source directory/file."),
         Param("op", "str", "Operation: 'list', 'test', 'create', or 'extract' (default 'list').", required=False, default="list"),
         Param("target", "str", "Destination path or extraction directory.", required=False),
         Param("archive_format", "str", "Archive format for create: 'zip', 'tar.gz', 'tar.bz2', 'tar.xz' (default 'zip').", required=False, default="zip"),
         Param("level", "int", "Compression level from 0 to 9 (default 6).", required=False, default=6),
         Param("files", "list", "Specific file paths to pack for op='create' (default: the whole source directory).", required=False)),
        category="files",
        examples=({"path": "~/backup.zip", "op": "list"},
                  {"path": "~/archive.tar.gz", "op": "test"},
                  {"path": "~/my_project", "target": "~/project.zip", "op": "create"},
                  {"path": "~/data.zip", "target": "~/data_unpacked", "op": "extract"}),
    ),
    Action(
        "data_validate", "Validate JSON data structures against schemas, infer JSON schemas, "
        "compute deep structural diffs, or sanitize payloads. "
        "Supports types (string, integer, number, boolean, array, object, null) and constraints "
        "(required, enum, minimum, maximum, pattern, minLength, maxLength, properties, items). "
        "Operations: 'validate' (schema validation), 'infer' (generate JSON schema from data), "
        "'diff' (deep object diff), 'sanitize' (clean whitespace and types based on schema).",
        (Param("op", "str", "Operation: 'validate', 'infer', 'diff', or 'sanitize' (default 'validate').", required=False, default="validate"),
         Param("data", "str", "JSON string, JSON file path, or object to validate/inspect."),
         Param("schema", "str", "JSON schema definition string, schema file path, or schema object.", required=False),
         Param("target", "str", "Comparison target for op='diff'.", required=False)),
        category="coding",
        examples=({"op": "validate", "data": "config.json", "schema": "schema.json"},
                  {"op": "infer", "data": "sample.json"},
                  {"op": "diff", "data": "old_config.json", "target": "new_config.json"},
                  {"op": "sanitize", "data": "payload.json", "schema": "schema.json"}),
    ),
    Action(
        "crypto_intel", "Compute cryptographic hashes, verify checksums, generate HMAC signatures, "
        "and produce secure random tokens/keys (SHA-256, SHA-512, MD5, BLAKE2, PBKDF2). "
        "Operations: 'hash' (checksum file/text), 'verify' (constant-time checksum match), "
        "'hmac' (keyed signature), 'token' (cryptographic hex, uuid4, password), 'pbkdf2' (salted password derivation).",
        (Param("op", "str", "Operation: 'hash', 'verify', 'hmac', 'token', or 'pbkdf2' (default 'hash').", required=False, default="hash"),
         Param("target", "str", "Target text, file path, or token type.", required=False, default=""),
         Param("algo", "str", "Hash algorithm: 'sha256', 'sha512', 'md5', 'blake2b' (default 'sha256').", required=False, default="sha256"),
         Param("key", "str", "Secret key for op='hmac'.", required=False),
         Param("expected", "str", "Expected hash digest for op='verify'.", required=False),
         Param("length", "int", "Token length in bytes/chars for op='token' (default 32).", required=False, default=32),
         Param("salt", "str", "Hex salt for op='pbkdf2' (a secure one is generated when omitted).", required=False),
         Param("iterations", "int", "PBKDF2 iteration count for op='pbkdf2' (default 100000, minimum 1000).", required=False, default=100000, minimum=1000)),
        category="security",
        examples=({"op": "hash", "target": "~/download.iso", "algo": "sha256"},
                  {"op": "verify", "target": "~/file.zip", "expected": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"},
                  {"op": "hmac", "target": "webhook payload", "key": "secret_key"},
                  {"op": "token", "target": "hex", "length": 32},
                  {"op": "token", "target": "password", "length": 24}),
    ),
    Action(
        "diff_patch", "Compute unified text diffs, similarity ratios, line statistics, "
        "and safely apply unified diff patches to files. "
        "Operations: 'diff' (unified diff between files/strings), 'similarity' (sequence matcher ratio), "
        "'stats' (lines added/deleted/modified count), 'patch' (apply unified patch to file with dry_run).",
        (Param("op", "str", "Operation: 'diff', 'similarity', 'stats', or 'patch' (default 'diff').", required=False, default="diff"),
         Param("source", "str", "Original file path or text string (or patch content for op='patch').", required=False, default=""),
         Param("target", "str", "New file path or text string to compare with (or target file for op='patch').", required=False, default=""),
         Param("patch_text", "str", "Unified patch content for op='patch'.", required=False),
         Param("dry_run", "bool", "Test patch without modifying file for op='patch' (default False).", required=False, default=False)),
        category="coding",
        examples=({"op": "diff", "source": "v1.py", "target": "v2.py"},
                  {"op": "similarity", "source": "draft1.txt", "target": "draft2.txt"},
                  {"op": "stats", "source": "old.py", "target": "new.py"},
                  {"op": "patch", "target": "app.py", "patch_text": "@@ -1,3 +1,3 @@\n-print('hi')\n+print('hello')", "dry_run": True}),
    ),
    Action(
        "regex_intel", "Test regex pattern validity, extract matches with character spans & named groups, "
        "perform regex substitutions, and automatically redact sensitive PII/secrets. "
        "Operations: 'extract' (find all matches/groups/spans), 'test' (verify regex compilation and match), "
        "'replace' (regex substitution with group backreferences), 'redact' (mask emails, IPs, API keys, cards, phones).",
        (Param("op", "str", "Operation: 'extract', 'test', 'replace', or 'redact' (default 'extract').", required=False, default="extract"),
         Param("pattern", "str", "Regular expression pattern.", required=False, default=""),
         Param("text", "str", "Input text or file path to evaluate.", required=False, default=""),
         Param("replacement", "str", "Replacement text for op='replace'.", required=False, default=""),
         Param("preset", "str", "Redaction preset for op='redact': 'all', 'email', 'ipv4', 'jwt', 'api_key', 'credit_card', 'phone' (default 'all').", required=False, default="all"),
         Param("flags", "str", "Regex flags string (e.g. 'i' for ignorecase, 'm' for multiline, 's' for dotall).", required=False, default="")),
        category="coding",
        examples=({"op": "test", "pattern": r"^v\d+\.\d+\.\d+$", "text": "v1.2.3"},
                  {"op": "extract", "pattern": r"(?P<key>\w+)=(?P<val>\w+)", "text": "user=admin env=prod"},
                  {"op": "replace", "pattern": r"\bfoo\b", "text": "foo bar foo", "replacement": "baz"},
                  {"op": "redact", "text": "Contact support@example.com with API key sk-12345678901234567890"}),
    ),
    Action(
        "api_mock", "Spawn in-process HTTP mock servers, configure mock endpoints, "
        "simulate API responses with custom headers/status codes/delays, and inspect recorded request history. "
        "Operations: 'start' (launch mock server on port), 'route' (register endpoint mock rule), "
        "'history' (inspect captured HTTP requests), 'clear' (clear logs/routes), 'stop' (shut down server).",
        (Param("op", "str", "Operation: 'start', 'route', 'history', 'clear', or 'stop' (default 'start').", required=False, default="start"),
         Param("port", "int", "Local port number (default 8999).", required=False, default=8999),
         Param("path", "str", "Endpoint URL path (default '/').", required=False, default="/"),
         Param("method", "str", "HTTP method: 'GET', 'POST', 'PUT', 'DELETE', or '*' (default 'GET').", required=False, default="GET"),
         Param("status", "int", "HTTP response status code (default 200).", required=False, default=200),
         Param("body", "str", "Response body text, JSON object, or file path.", required=False, default=""),
         Param("delay", "float", "Artificial latency delay in seconds (default 0.0).", required=False, default=0.0)),
        category="coding",
        examples=({"op": "start", "port": 8999},
                  {"op": "route", "port": 8999, "path": "/api/user", "method": "GET", "body": {"id": 1, "name": "Alice"}},
                  {"op": "route", "port": 8999, "path": "/webhook", "method": "POST", "status": 201},
                  {"op": "history", "port": 8999},
                  {"op": "stop", "port": 8999}),
    ),
    Action(
        "cron_intel", "Validate cron expressions, translate cron syntax into natural English explanations, "
        "and calculate upcoming execution timestamps across timezones. "
        "Operations: 'explain' (describe cron schedule in English), 'next' (generate next N occurrence timestamps), "
        "'validate' (validate 5-part cron syntax and field ranges).",
        (Param("op", "str", "Operation: 'explain', 'next', or 'validate' (default 'explain').", required=False, default="explain"),
         Param("expr", "str", "Standard 5-part cron expression (default '* * * * *').", required=False, default="* * * * *"),
         Param("count", "int", "Number of upcoming occurrences to compute for op='next' (default 5).", required=False, default=5),
         Param("timezone_name", "str", "IANA timezone name (default 'UTC').", required=False, default="UTC"),
         Param("base_time", "str", "Base start timestamp in ISO-8601 format for op='next'.", required=False, default="")),
        category="coding",
        examples=({"op": "explain", "expr": "*/15 9-17 * * 1-5"},
                  {"op": "next", "expr": "0 9 * * 1", "count": 3, "timezone_name": "America/New_York"},
                  {"op": "validate", "expr": "0 0 1 1 *"}),
    ),
    # ---- clipboard -------------------------------------------------------
    Action(
        "system_status", "Report machine diagnostics (CPU, memory, disk, "
        "battery, uptime). Use to answer 'how's my system / battery / cpu / "
        "memory / disk' without opening any app.", (),
        category="system", examples=({},),
    ),
    Action(
        "system_diagnostics", "Run comprehensive environmental diagnostics, "
        "subsystem health inspection, database integrity checks, and automated self-repair. "
        "Checks Python dependencies, AI Brain credentials, screen resolution, browser engine, "
        "memory database integrity (PRAGMA integrity_check), and credential vault encryption. "
        "Operations: 'health' (full subsystem audit), 'repair' (auto-optimize and vacuum databases).",
        (Param("op", "str", "Operation: 'health', 'check', or 'repair' (default 'health').", required=False, default="health"),
         Param("auto_fix", "bool", "Automatically apply self-repair fixes during check (default False).", required=False, default=False)),
        category="system",
        examples=({"op": "health"},
                  {"op": "repair"},
                  {"op": "health", "auto_fix": True}),
    ),
    Action(
        "net_intel", "Execute network, port, DNS, and TLS/SSL diagnostics. "
        "Operations: 'port_check' (probe TCP port reachability and measure latency), "
        "'dns' (resolve IPv4/IPv6 addresses and canonical names), "
        "'ssl' (inspect TLS/SSL certificates, expiration dates, and SANs), "
        "'listening' (enumerate local active listening TCP ports).",
        (Param("op", "str", "Operation: 'port_check', 'dns', 'ssl', or 'listening' (default 'port_check').", required=False, default="port_check"),
         Param("host", "str", "Target hostname or IP address (default '127.0.0.1').", required=False, default="127.0.0.1"),
         Param("port", "int", "Target port number for port_check/ssl (default 80 or 443).", required=False, default=80),
         Param("timeout", "int", "Socket timeout in seconds (default 3).", required=False, default=3)),
        category="system",
        examples=({"op": "port_check", "host": "127.0.0.1", "port": 8000},
                  {"op": "dns", "host": "google.com"},
                  {"op": "ssl", "host": "github.com", "port": 443},
                  {"op": "listening"}),
    ),
    Action(
        "process_intel", "Monitor system processes, inspect CPU/memory resource usage, "
        "search active tasks, and safely terminate processes with OS protection. "
        "Operations: 'list' / 'top' (enumerate top processes sorted by memory or CPU), "
        "'inspect' (deep metadata for specific PID), 'find' (search processes by name substring), "
        "'terminate' (terminate process or process tree with safety blacklist protection).",
        (Param("op", "str", "Operation: 'list', 'inspect', 'find', or 'terminate' (default 'list').", required=False, default="list"),
         Param("pid", "int", "Target Process ID for inspect/terminate.", required=False),
         Param("name", "str", "Process name or search substring for find/terminate.", required=False, default=""),
         Param("sort_by", "str", "Sort metric for op='list': 'memory' or 'cpu' (default 'memory').", required=False, default="memory"),
         Param("limit", "int", "Maximum number of processes to return (default 20).", required=False, default=20),
         Param("force", "bool", "Forcefully terminate process for op='terminate' (default False).", required=False, default=False)),
        category="system",
        examples=({"op": "list", "sort_by": "memory", "limit": 10},
                  {"op": "inspect", "pid": 1234},
                  {"op": "find", "name": "python"},
                  {"op": "terminate", "name": "stuck_worker.exe"}),
    ),
    Action(
        "web_search", "Search the web with DuckDuckGo and get back text "
        "results (an instant answer plus top links). Use to look something up "
        "and answer directly, without opening a browser.",
        (Param("query", "str", "What to search for."),
         Param("max_results", "int", "How many results (default 5).",
               required=False, default=5)),
        category="system",
        examples=({"query": "who won the 2022 world cup"},
                  {"query": "python read a file", "max_results": 3}),
    ),
    Action(
        "extract_web_data", "Extract structured data from web pages and HTML documents. "
        "Modes: 'tables' (parses all HTML tables into structured JSON rows or CSV), "
        "'metadata' (extracts title, meta tags, OpenGraph, and JSON-LD microdata schemas), "
        "'links' (extracts internal links, external links, and downloadable files like PDF/ZIP/CSV), "
        "'article' (extracts clean readable article text with boilerplate nav/footers stripped).",
        (Param("url", "str", "URL of the webpage to scrape and extract data from.", required=False),
         Param("html_content", "str", "Raw HTML string to parse directly instead of fetching URL.", required=False),
         Param("mode", "str", "Extraction mode: 'tables', 'metadata', 'links', or 'article' (default 'tables').", required=False, default="tables"),
         Param("output_format", "str", "Output format for tables: 'json' or 'csv' (default 'json').", required=False, default="json")),
        category="system",
        examples=({"url": "https://en.wikipedia.org/wiki/List_of_countries_by_GDP_(nominal)", "mode": "tables", "output_format": "csv"},
                  {"url": "https://news.ycombinator.com", "mode": "links"},
                  {"url": "https://github.com/trending", "mode": "metadata"}),
    ),
    Action(
        "media_intel", "Inspect audio and media files, compute waveform energy envelopes, "
        "detect speech/silence intervals, and slice audio segments (WAV, MP3, FLAC, MP4). "
        "Operations: 'info' (container, duration, sample rate, channels, bit depth, bitrate), "
        "'waveform' (RMS energy envelope and peak amplitude), 'silence' (speech vs silence timestamps), "
        "'slice' (extract segment from start_sec to end_sec into target path).",
        (Param("path", "str", "Path to audio or media file."),
         Param("op", "str", "Operation: 'info', 'waveform', 'silence', or 'slice' (default 'info').", required=False, default="info"),
         Param("target", "str", "Output path for sliced audio with op='slice'.", required=False),
         Param("start_sec", "float", "Start timestamp in seconds for op='slice' (default 0.0).", required=False, default=0.0),
         Param("end_sec", "float", "End timestamp in seconds for op='slice' (default 0.0).", required=False, default=0.0),
         Param("threshold_db", "float", "Silence threshold in decibels for op='silence' (default -40.0).", required=False, default=-40.0)),
        category="system",
        examples=({"path": "~/recording.wav", "op": "info"},
                  {"path": "~/speech.wav", "op": "waveform"},
                  {"path": "~/meeting.wav", "op": "silence"},
                  {"path": "~/podcast.wav", "op": "slice", "start_sec": 10.5, "end_sec": 35.0, "target": "~/clip.wav"}),
    ),
    Action(
        "schedule_task", "Schedule a task to run automatically later or on a "
        "repeat (a cron job). schedule accepts 'every N minutes/hours', 'daily "
        "at HH:MM', 'in N minutes', or 'at HH:MM' (24h).",
        (Param("schedule", "str", "When to run, e.g. 'daily at 08:00'."),
         Param("command", "str", "The task to run when it fires.")),
        category="system",
        examples=({"schedule": "daily at 08:00", "command": "search the web for today's news"},
                  {"schedule": "every 30 minutes", "command": "tell me the system status"}),
    ),
    Action(
        "media", "Control audio and media playback without any GUI clicking. "
        "op is one of: play_pause, next, prev, mute, volume_up, volume_down, "
        "set_volume.",
        (Param("op", "str", "One of: play_pause, next, prev, mute, volume_up, "
               "volume_down, set_volume."),
         Param("value", "int", "Percent 0-100: target level for set_volume, "
               "step size for volume_up/down (default 10).", required=False)),
        category="system",
        examples=({"op": "set_volume", "value": 40}, {"op": "play_pause"},
                  {"op": "mute"}),
    ),
    Action(
        "notify", "Show a Windows toast notification to the user (visible even "
        "when they are working in another app). Use it to report the result of "
        "a scheduled or long-running task.",
        (Param("message", "str", "Notification text."),
         Param("title", "str", "Notification title (default 'JARVIS').",
               required=False, default="JARVIS")),
        category="system", examples=({"message": "Download finished"},),
    ),
    Action(
        "take_screenshot", "Capture the whole screen and save it as a PNG "
        "image file.",
        (Param("path", "str", "Where to save; defaults to a timestamped file "
               "in ~/Pictures.", required=False),),
        category="system",
        examples=({}, {"path": "~/Pictures/before.png"}),
    ),
    Action(
        "clipboard_read", "Read the current clipboard text.", (),
        category="system", examples=({},),
    ),
    Action(
        "clipboard_write", "Put text on the clipboard.",
        (Param("text", "str", "Text to copy."),),
        category="system", examples=({"text": "copied text"},),
    ),
    Action(
        "remember", "Store a fact, preference, rule, or piece of context in "
        "persistent memory so you remember it FOREVER across sessions.",
        (Param("fact", "str", "The exact fact, preference, or rule to remember forever."),
         Param("category", "str", "Category or tag, e.g. 'preference', 'fact', "
               "'user_info', 'rule', 'project' (default 'fact').",
               required=False, default="fact"),
         Param("entity", "str", "Subject entity to file the fact under in the "
               "knowledge graph, e.g. 'user' or a project name.", required=False),
         Param("relation", "str", "Relation from entity to target_entity, "
               "e.g. 'prefers', 'works_on'.", required=False),
         Param("target_entity", "str", "Object entity the relation points at "
               "(used together with entity and relation).", required=False)),
        category="system",
        examples=({"fact": "User prefers dark mode UI and concise responses", "category": "preference"},
                  {"fact": "Project root is C:/Users/Administrator/Jarvis", "category": "project"}),
    ),
    Action(
        "forget", "Remove a stored fact or memory from persistent memory.",
        (Param("target", "str", "The keyword or text of the memory to remove."),),
        category="system",
        examples=({"target": "dark mode"},),
    ),
    Action(
        "memory_search", "Search permanent long-term memory semantically via Vector RAG.",
        (Param("query", "str", "Search query, topic, or question to find relevant facts/plans for."),
         Param("top_k", "int", "Max results to return (default 5).", required=False, default=5)),
        category="system",
        examples=({"query": "coding preferences"}, {"query": "git repository location"}),
    ),
    Action(
        "graph_query", "Query entity connections and relationships in the Knowledge Graph.",
        (Param("entity", "str", "Name of the entity to query connections for (e.g. 'User', 'Spotify', 'Jarvis')."),),
        category="system",
        examples=({"entity": "Spotify"}, {"entity": "User"}),
    ),
    Action(
        "voice_control", "Control voice engine, interrupt active playback, or toggle full-duplex barge-in.",
        (Param("action", "str", "One of: 'interrupt', 'status', 'enable_duplex', 'disable_duplex', 'set_sensitivity'."),
         Param("value", "str", "Optional value, e.g. sensitivity float 0.1-1.0.", required=False)),
        category="system",
        examples=({"action": "interrupt"}, {"action": "enable_duplex"}, {"action": "set_sensitivity", "value": "0.7"}),
    ),
    Action(
        "macro", "Watch & Learn Macro recorder and playback engine. Record desktop actions or execute learned macros.",
        (Param("action", "str", "One of: 'record', 'stop', 'play', 'list', 'show', 'delete'."),
         Param("name", "str", "Macro name (e.g. 'open_daily_report', 'send_invoice').", required=False),
         Param("description", "str", "Description of what the macro accomplishes.", required=False),
         Param("speed", "float", "Playback speed multiplier (e.g. 1.0 = normal, 2.0 = 2x speed).", required=False, default=1.0),
         Param("params", "dict", "Optional dictionary of parameter values to substitute.", required=False)),
        category="control",
        examples=({"action": "record", "name": "open_sales_sheet", "description": "Open Chrome and go to sales dashboard"},
                  {"action": "stop"},
                  {"action": "play", "name": "open_sales_sheet", "speed": 1.5},
                  {"action": "list"}),
    ),
    Action(
        "skill", "Search, learn, use and write Jarvis Skills - reusable presets "
        "that say HOW to do a whole class of task (research briefs, driving a "
        "desktop app, triage, drafting, inbox handling). Search before improvising "
        "a multi-step job, load the skill that fits to bring its full steps into "
        "context, and keep it loaded while you work. A skill is instructions, not "
        "permission: it can never grant a capability, and the safety rules always "
        "outrank it. After you work out a procedure worth repeating, save it with "
        "action='create' so the next run is a lookup instead of a rediscovery.",
        (Param("action", "str", "One of: 'list', 'search', 'show', 'load', 'unload', "
               "'create', 'update', 'delete'."),
         Param("name", "str", "Skill name or slug (for show/load/unload/update/delete).",
               required=False),
         Param("query", "str", "What you are trying to do (for search).", required=False),
         Param("description", "str", "One line on what the skill is for (create/update).",
               required=False),
         Param("when_to_use", "str", "Comma-separated triggers: when to reach for it "
               "(create/update).", required=False),
         Param("body", "str", "The skill's markdown instructions (create/update).",
               required=False),
         Param("tools", "str", "Comma-separated tool names the skill uses (create/update).",
               required=False)),
        category="control",
        examples=(({"action": "search", "query": "summarise my inbox"},
                  {"action": "load", "name": "research-brief"},
                  {"action": "create", "name": "weekly-report",
                   "description": "Build the weekly numbers report from the sales sheet.",
                   "when_to_use": "weekly report, sales numbers, monday summary",
                   "body": "1. Open the sheet...\n2. ..."},
                  {"action": "list"})),
    ),
    # ---- remote devices --------------------------------------------------
    Action(
        "remote_task", "Send a task to an explicitly named, trusted paired "
        "device. The task runs only in that device's locally started Jarvis "
        "Remote agent; use this ONLY when the user clearly asks to control that "
        "named remote device. Obey the device capabilities shown in the paired-"
        "device prompt and never invent remote tools. Android accepts only: "
        "open <app or URL>, screenshot, capabilities, tap element <id>, long "
        "press element <id>, type element <id> <text>, scroll element <id> "
        "<forward|backward>, swipe element <id> <direction>, raw-coordinate "
        "fallbacks, back, and home. ALWAYS request an Android screenshot before "
        "a UI action, use an ID from its exact MOBILE UI ELEMENTS list, and "
        "request a fresh screenshot after the screen changes. Never estimate "
        "mobile coordinates from the compressed preview. A successful Android "
        "screenshot is attached to the next vision turn automatically.",
        (Param("device", "str", "Exact name of the paired remote device."),
         Param("task", "str", "The task to perform on that remote device."),
         Param("timeout", "int", "Seconds to wait for its result (5-600; default configured).",
               required=False)),
        category="remote",
        examples=({"device": "Office PC", "task": "Open Notepad and type the shopping list."},),
    ),
    # ---- connectors -------------------------------------------------------
    Action(
        "connector", "Read the user's own accounts DIRECTLY - Gmail, Discord "
        "and WhatsApp - in one fast call, with no browser, no clicking and no "
        "screenshots. ALWAYS prefer this over opening the website or app when "
        "the user asks what mail/messages they have. Services and their ops:\n"
        "      gmail    - unread | search (query = Gmail search syntax) | read "
        "(target = an [id] from a previous listing)\n"
        "      discord  - guilds | channels (target = server) | messages "
        "(target = channel id or #name)\n"
        "      whatsapp - messages | profile\n"
        "      A service that is not set up replies with the exact .env "
        "variables it needs - relay that to the user rather than guessing.",
        (Param("service", "str", "One of: gmail, discord, whatsapp."),
         Param("op", "str", "What to do on that service (see the list above), "
               "e.g. 'unread', 'search', 'read', 'messages', 'channels'."),
         Param("query", "str", "Search text. For gmail this is Gmail search "
               "syntax (from:, is:unread, newer_than:7d, has:attachment).",
               required=False),
         Param("target", "str", "Which thing to act on: a gmail message id, a "
               "discord channel id or #name, or a discord server name.",
               required=False),
         Param("limit", "int", "How many items to return (default 10, max 50).",
               required=False, default=10)),
        category="connectors",
        examples=({"service": "gmail", "op": "unread"},
                  {"service": "gmail", "op": "search",
                   "query": "from:github is:unread", "limit": 5},
                  {"service": "gmail", "op": "read", "target": "24817"},
                  {"service": "discord", "op": "messages", "target": "#general"},
                  {"service": "whatsapp", "op": "messages"}),
    ),
    # ---- mcp --------------------------------------------------------------
    Action(
        "mcp", "Configure your OWN MCP (Model Context Protocol) servers - the "
        "same connectors Claude Desktop uses - to gain whole new tool sets "
        "(GitHub, Slack, databases, browser automation, etc.). op is one of: "
        "list, add, remove, enable, disable, tools. Added servers persist and "
        "their tools appear in the MCP TOOLS list for you to call with mcp_call.",
        (Param("op", "str", "One of: list, add, remove, enable, disable, tools."),
         Param("name", "str", "Server name (for add/remove/enable/disable/tools).",
               required=False),
         Param("command", "str", "Executable that launches the server (for add), "
               "e.g. 'npx', 'uvx', 'python'.", required=False),
         Param("args", "list", "Arguments passed to the command (for add).",
               required=False),
         Param("env", "dict", "Environment variables for the server, e.g. API "
               "keys/tokens it needs (for add).", required=False)),
        category="mcp",
        examples=({"op": "list"},
                  {"op": "add", "name": "filesystem", "command": "npx",
                   "args": ["-y", "@modelcontextprotocol/server-filesystem",
                            "C:/Users"]},
                  {"op": "tools", "name": "filesystem"}),
    ),
    Action(
        "mcp_call", "Call a tool exposed by one of your configured MCP servers "
        "(see the MCP TOOLS list in the prompt). Use it for anything an MCP "
        "connector provides that your built-in actions do not.",
        (Param("server", "str", "The MCP server name."),
         Param("tool", "str", "The tool to call on that server."),
         Param("arguments", "dict", "Arguments object for the tool (match its "
               "schema).", required=False)),
        category="mcp",
        examples=({"server": "github", "tool": "search_repositories",
                   "arguments": {"query": "jarvis desktop assistant"}},),
    ),
    # ---- security --------------------------------------------------------
    Action(
        "secret", "Securely store, retrieve, delete or migrate credentials, "
        "API keys, and service tokens in Windows Credential Manager / DPAPI vault. "
        "op is one of: 'set', 'get', 'list', 'delete', 'migrate'.",
        (Param("op", "str", "One of: 'set', 'get', 'list', 'delete', 'migrate'."),
         Param("key", "str", "Secret key name (e.g. 'OPENAI_API_KEY', 'WEATHER_API_KEY').", required=False),
         Param("value", "str", "Secret value or token (for 'set').", required=False),
         Param("backend", "str", "Target backend: 'credman' (Windows Credential Manager), "
               "'dpapi' (Windows DPAPI encrypted vault), or 'all'.", required=False, default="credman")),
        category="system",
        examples=({"op": "list"},
                  {"op": "set", "key": "WEATHER_API_KEY", "value": "xyz123abc456"},
                  {"op": "get", "key": "OPENAI_API_KEY"},
                  {"op": "migrate"}),
    ),
    # ---- perception / vision ---------------------------------------------
    Action(
        "see", "Live screen and webcam 'See What I See' multimodal visual perception. "
        "Captures real-time desktop screen and/or physical user webcam frames, "
        "and visually analyzes, describes, reads, or inspects them.",
        (Param("prompt", "str", "Question or instruction for visual analysis (e.g. "
               "'What is on my screen?', 'Read the document held to the webcam', "
               "'Describe the physical room and desk').", required=False, default="What do you see?"),
         Param("source", "str", "Vision input source: 'both' (screen + webcam dual fusion), "
               "'webcam' (user camera only), or 'screen' (desktop screen only).",
               required=False, default="both"),
         Param("camera", "int", "Webcam index (default 0).", required=False, default=0)),
        category="system",
        examples=({"prompt": "Describe what is currently on my screen and desk.", "source": "both"},
                  {"prompt": "Read the text written on the whiteboard in my webcam view.", "source": "webcam"},
                  {"prompt": "What error message is visible in the terminal?", "source": "screen"}),
    ),
    # ---- meta ------------------------------------------------------------
    Action(
        "wait", "Pause briefly to let the screen settle after an action.",
        (Param("seconds", "float", "Seconds to wait (0-10).",
               required=False, default=1.0, minimum=0, maximum=10),),
        category="meta", examples=({"seconds": 1.5},),
    ),
    Action(
        "wait_for", "Wait until a window title or on-screen element containing "
        "'target' appears (re-checks every second, up to timeout). Use after "
        "launching an app or loading a page instead of guessing with wait.",
        (Param("target", "str", "Case-insensitive text to wait for."),
         Param("timeout", "float", "Max seconds to wait (default 10, max 30).",
               required=False, default=10.0)),
        category="meta",
        examples=({"target": "Notepad"}, {"target": "Save As", "timeout": 15}),
    ),
    Action(
        "observe", "Take a fresh screenshot and re-read the screen. Use after an "
        "action changes the UI and you need to see the result.", (),
        category="meta", examples=({},),
    ),
    Action(
        "finish", "The task is complete. Provide a short result summary for the user.",
        (Param("summary", "str", "What was accomplished."),),
        category="meta", terminal=True,
        examples=({"summary": "Saved the note to todo.txt."},),
    ),
    Action(
        "set_theme", "Configure CLI terminal UI appearance, formatting style or theme color.",
        (Param("theme", "str", "Theme name or style (e.g. 'arc', 'cyan', 'neon', 'dark').", required=False, default="arc"),),
        category="system", examples=({"theme": "arc"},),
    ),
    Action(
        "ask", "Ask the user a question when the task is ambiguous or blocked. "
        "In an interactive session their answer comes back and you continue "
        "the task; otherwise the run ends. Only use when you genuinely cannot "
        "proceed.",
        (Param("question", "str", "The question for the user."),),
        category="meta", terminal=True,
        examples=({"question": "Which file did you mean, report.docx or report.pdf?"},),
    ),
    Action(
        "stop_session", "End this Jarvis session and shut the runtime down. "
        "This is not 'finish': finish ends the current task and hands the "
        "session back to the user, while this ends the session itself. Use it "
        "only when the user asks you to stop, close, quit or shut Jarvis "
        "down, or when continuing is unsafe - never as a way to end a task "
        "you could still complete. Because the session closes, 'reason' is "
        "the last thing the user reads, so state what you did and whether "
        "anything was left unfinished.",
        (Param("reason", "str", "Short closing note shown to the user, e.g. "
               "'Shutting down now; the report was saved first.'",
               required=False, default=""),),
        category="meta",
        examples=(({"reason": "Shutting Jarvis down as requested."},
                   {"reason": "Stopping before the next step: the operation "
                              "needs your confirmation."}),)
    ),
)


ACTIONS_BY_NAME: dict[str, Action] = {a.name: a for a in ACTIONS}


def action_names() -> list[str]:
    return [a.name for a in ACTIONS]


def to_json_schema() -> list[dict]:
    """Return an OpenAI/JSON-schema style description of every action.

    Handy for OpenAI-compatible function-calling backends and for docs.
    """
    out: list[dict] = []
    for a in ACTIONS:
        props: dict[str, Any] = {}
        required: list[str] = []
        for p in a.params:
            prop: dict[str, Any] = {
                "type": _json_type(p.type),
                "description": p.description,
            }
            if p.minimum is not None:
                prop["minimum"] = p.minimum
            if p.maximum is not None:
                prop["maximum"] = p.maximum
            if p.type == "list":
                # Function-declaration schemas require ARRAY parameters to
                # declare their item shape. Most Jarvis lists contain strings;
                # write_files is the one structured-list action.
                if a.name == "write_files" and p.name == "files":
                    prop["items"] = {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["path", "content"],
                    }
                else:
                    prop["items"] = {"type": "string"}
            props[p.name] = prop
            if p.required:
                required.append(p.name)
        out.append({
            "name": a.name,
            "description": a.summary,
            "parameters": {"type": "object", "properties": props, "required": required},
        })
    return out


def gemini_safe_json_schema() -> list[dict]:
    """The action schema restricted to what Gemini's functionDeclarations accept.

    Gemini function calling accepts only a subset of OpenAPI Schema: unknown
    property fields (``minimum``, ``maximum``, ``default``) are rejected with a
    400 INVALID_ARGUMENT that would take down the recovery path for ALL 89
    actions at once. Bounds are therefore folded into the description text —
    the model still sees them, the validator does not. The full JSON view
    (:func:`to_json_schema`) is unchanged; this only narrows what one transport
    receives. Keep in step with the accepted-field list in the Gemini docs.
    """
    out: list[dict] = []
    for entry in to_json_schema():
        props: dict[str, Any] = {}
        for pname, prop in entry["parameters"]["properties"].items():
            lo = prop.pop("minimum", None)
            hi = prop.pop("maximum", None)
            if lo is not None or hi is not None:
                lo_s = "-inf" if lo is None else _fmt_num(lo)
                hi_s = "+inf" if hi is None else _fmt_num(hi)
                prop["description"] = (
                    prop.get("description", "") + f" (range {lo_s}..{hi_s})"
                ).strip()
            props[pname] = prop
        out.append({
            "name": entry["name"],
            "description": entry["description"],
            "parameters": {
                "type": entry["parameters"]["type"],
                "properties": props,
                "required": entry["parameters"]["required"],
            },
        })
    return out


def _fmt_num(v: Any) -> str:
    """1.0 -> '1', -50 -> '-50', 0.5 -> '0.5'."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return str(int(f)) if f.is_integer() else f"{f:g}"


def _json_type(t: str) -> str:
    return {"int": "integer", "float": "number", "str": "string",
            "bool": "boolean", "list": "array", "dict": "object"}.get(t, "string")
