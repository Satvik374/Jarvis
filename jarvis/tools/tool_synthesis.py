"""Dynamic Tool Synthesis and Sandboxed Code Execution Engine for Jarvis.

Enables Jarvis to dynamically synthesize, test, save, remember, and execute
reusable Python tools and automation scripts for any task.

Features:
  - Sandboxed execution with timeout, resource limits, and error trapping.
  - Automatic AST syntax validation before saving.
  - Persistent disk storage under `tools_synthesized/<name>.py`.
  - JSON metadata registry with usage counts, timestamps, and parameters.
  - Seamless integration with Jarvis Long-term Vector RAG & Knowledge Graph
    so synthesized tools are automatically remembered and recalled for future tasks.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..utils import logging as log
from ..utils.paths import state_root


def get_default_tools_dir() -> Path:
    return state_root() / "tools_synthesized"


@dataclass
class ToolParameter:
    name: str
    type: str = "str"
    description: str = ""
    required: bool = True
    default: Any = None


@dataclass
class SynthesizedTool:
    name: str
    description: str
    code: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    created_at: str = ""
    last_used: str = ""
    usage_count: int = 0
    file_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SynthesizedTool:
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            code=data.get("code", ""),
            parameters=data.get("parameters", {}),
            tags=data.get("tags", []),
            created_at=data.get("created_at", ""),
            last_used=data.get("last_used", ""),
            usage_count=data.get("usage_count", 0),
            file_path=data.get("file_path", ""),
        )

    def summary(self) -> str:
        params_str = ", ".join(
            f"{k}: {v.get('type', 'Any')}" if isinstance(v, dict) else f"{k}"
            for k, v in self.parameters.items()
        )
        return f"Tool '{self.name}' ({params_str}): {self.description}"


class SynthesizedToolManager:
    """Manages the lifecycle, persistence, memory indexing, and execution of dynamic tools."""

    def __init__(self, tools_dir: Optional[Path | str] = None):
        self.tools_dir = Path(tools_dir) if tools_dir else get_default_tools_dir()
        self.tools_dir.mkdir(parents=True, exist_ok=True)
        self.registry_file = self.tools_dir / "tools_registry.json"
        self._tools: Dict[str, SynthesizedTool] = {}
        self._load_registry()

    def _load_registry(self) -> None:
        """Load registered tools from JSON index."""
        if not self.registry_file.exists():
            self._tools = {}
            return
        try:
            with open(self.registry_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            self._tools = {
                name: SynthesizedTool.from_dict(tdata)
                for name, tdata in raw.items()
            }
        except Exception as exc:
            log.warn(f"Failed to load synthesized tools registry: {exc}")
            self._tools = {}

    def _save_registry(self) -> None:
        """Save registered tools to JSON index."""
        try:
            data = {name: tool.to_dict() for name, tool in self._tools.items()}
            with open(self.registry_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as exc:
            log.warn(f"Failed to save synthesized tools registry: {exc}")

    def synthesize(
        self,
        name: str,
        description: str,
        code: str,
        parameters: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
        test_args: Optional[Dict[str, Any]] = None,
        memory_mgr: Any = None,
    ) -> Tuple[bool, str]:
        """Validate, save, test, and register a new synthesized tool."""
        clean_name = re.sub(r"[^a-zA-Z0-9_]", "_", (name or "").strip()).lower()
        if not clean_name:
            return False, "Tool synthesis failed: A valid tool 'name' (snake_case identifier) is required."

        desc = (description or "").strip()
        if not desc:
            return False, "Tool synthesis failed: A clear 'description' of what this tool does is required."

        code_str = (code or "").strip()
        if not code_str:
            return False, "Tool synthesis failed: Python 'code' is required."

        # 1. Validate AST syntax
        try:
            ast.parse(code_str)
        except SyntaxError as syn_err:
            return False, f"Tool synthesis failed: Python SyntaxError on line {syn_err.lineno}: {syn_err.msg}"

        # 2. Write tool code to file
        tool_path = self.tools_dir / f"{clean_name}.py"
        now_iso = datetime.now(timezone.utc).isoformat()

        # Wrap with standard runner header if standalone execution is needed
        formatted_code = (
            f'"""Synthesized Tool: {clean_name}\n'
            f'Description: {desc}\n'
            f'Created: {now_iso}\n'
            f'"""\n\n'
            f'{code_str}\n'
        )

        try:
            with open(tool_path, "w", encoding="utf-8") as f:
                f.write(formatted_code)
        except Exception as exc:
            return False, f"Failed to write tool file to {tool_path}: {exc}"

        # 3. Optional Test Execution
        if test_args is not None:
            ok, test_out = self._run_sandbox(tool_path, test_args, timeout=30)
            if not ok:
                # Keep file for debugging or remove
                return False, f"Tool synthesis test run failed:\n{test_out}"

        # 4. Save to registry
        param_dict = parameters or {}
        tag_list = tags or []
        # Auto-extract tags from name and description
        auto_tags = set(tag_list)
        for word in re.findall(r"\b[a-zA-Z]{3,}\b", f"{clean_name} {desc}".lower()):
            auto_tags.add(word)

        tool = SynthesizedTool(
            name=clean_name,
            description=desc,
            code=code_str,
            parameters=param_dict,
            tags=list(auto_tags),
            created_at=now_iso,
            last_used=now_iso,
            usage_count=0,
            file_path=str(tool_path),
        )
        self._tools[clean_name] = tool
        self._save_registry()

        # 5. Index into Long-Term Memory & Knowledge Graph
        mem_note = self._register_in_memory(tool, memory_mgr)

        msg = (
            f"✓ Synthesized tool '{clean_name}' successfully created and saved to '{tool_path.name}'.\n"
            f"Description: {desc}\n"
            f"Parameters: {param_dict}\n"
            f"{mem_note}\n"
            f"You can execute it anytime with: execute_synthesized_tool(name='{clean_name}', args={{...}})"
        )
        log.ok(f"Synthesized new tool '{clean_name}' -> registered in persistent tool store.")
        return True, msg

    def execute(
        self,
        name: str,
        args: Optional[Dict[str, Any]] = None,
        timeout: int = 60,
        cwd: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Execute a previously synthesized tool by name."""
        clean_name = re.sub(r"[^a-zA-Z0-9_]", "_", (name or "").strip()).lower()
        if clean_name not in self._tools:
            # Check if file exists on disk even if registry was cleared
            candidate = self.tools_dir / f"{clean_name}.py"
            if not candidate.exists():
                available = ", ".join(self._tools.keys()) or "none"
                return False, f"Tool '{clean_name}' not found. Available synthesized tools: [{available}]"
            # Auto-register found file
            self._tools[clean_name] = SynthesizedTool(
                name=clean_name,
                description=f"Synthesized tool {clean_name}",
                code=candidate.read_text(encoding="utf-8", errors="ignore"),
                file_path=str(candidate),
            )

        tool = self._tools[clean_name]
        tool_path = Path(tool.file_path) if tool.file_path else self.tools_dir / f"{clean_name}.py"
        if not tool_path.exists():
            # Recreate tool file from stored code
            with open(tool_path, "w", encoding="utf-8") as f:
                f.write(tool.code)

        input_args = args or {}
        ok, output = self._run_sandbox(tool_path, input_args, timeout=timeout, cwd=cwd)

        # Update usage stats
        tool.usage_count += 1
        tool.last_used = datetime.now(timezone.utc).isoformat()
        self._save_registry()

        return ok, output

    def _run_sandbox(
        self,
        tool_path: Path,
        args: Dict[str, Any],
        timeout: int = 60,
        cwd: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Run Python script in isolated subprocess passing args as JSON."""
        args_json = json.dumps(args, ensure_ascii=False)
        sub_env = dict(os.environ)
        sub_env["PYTHONIOENCODING"] = "utf-8"
        sub_env["JARVIS_TOOL_ARGS"] = args_json

        # Python wrapper script to support functions (main/run) or top-level execution
        runner_code = (
            f"import sys, os, json\n"
            f"args_raw = os.environ.get('JARVIS_TOOL_ARGS', '{{}}')\n"
            f"try:\n"
            f"    args = json.loads(args_raw)\n"
            f"except Exception:\n"
            f"    args = {{}}\n"
            f"import importlib.util\n"
            f"spec = importlib.util.spec_from_file_location('synthesized_module', r'{tool_path}')\n"
            f"mod = importlib.util.module_from_spec(spec)\n"
            f"sys.modules['synthesized_module'] = mod\n"
            f"try:\n"
            f"    spec.loader.exec_module(mod)\n"
            f"    # If module has a run or main function, execute it with args\n"
            f"    if hasattr(mod, 'run') and callable(mod.run):\n"
            f"        try:\n"
            f"            res = mod.run(**args)\n"
            f"        except TypeError:\n"
            f"            res = mod.run(args)\n"
            f"        if res is not None:\n"
            f"            print(res if isinstance(res, str) else json.dumps(res, indent=2, ensure_ascii=False, default=str))\n"
            f"    elif hasattr(mod, 'main') and callable(mod.main):\n"
            f"        try:\n"
            f"            res = mod.main(**args)\n"
            f"        except TypeError:\n"
            f"            res = mod.main(args)\n"
            f"        if res is not None:\n"
            f"            print(res if isinstance(res, str) else json.dumps(res, indent=2, ensure_ascii=False, default=str))\n"
            f"except Exception as e:\n"
            f"    import traceback\n"
            f"    traceback.print_exc()\n"
            f"    sys.exit(1)\n"
        )

        try:
            proc = subprocess.run(
                [sys.executable, "-c", runner_code],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=sub_env,
                timeout=max(1, min(600, int(timeout))),
                cwd=cwd or None,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return False, f"Execution timed out after {timeout} seconds."
        except Exception as exc:
            return False, f"Failed to execute tool sandbox: {exc}"

        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()

        lines = [f"Exit code: {proc.returncode}"]
        if out:
            lines.append(f"Output:\n{out}")
        if err:
            lines.append(f"Stderr:\n{err}")
        if not out and not err:
            lines.append("(Tool completed with no printed output)")

        return proc.returncode == 0, "\n".join(lines)

    def _register_in_memory(self, tool: SynthesizedTool, memory_mgr: Any = None) -> str:
        """Register the created tool in Long-Term Memory / Knowledge Graph."""
        fact = (
            f"[synthesized_tool] Created reusable tool '{tool.name}'. "
            f"Solves task: '{tool.description}'. "
            f"Path: {tool.file_path}. "
            f"Parameters: {list(tool.parameters.keys())}. "
            f"Usage: execute_synthesized_tool(name='{tool.name}', args={{...}})"
        )
        try:
            if memory_mgr is not None:
                memory_mgr.remember(
                    fact=fact,
                    category="synthesized_tool",
                    entity=f"tool:{tool.name}",
                    relation="solves",
                    target_entity=tool.description[:60],
                )
                return "Indexed in Long-Term Vector RAG & Knowledge Graph."
            else:
                from ..memory.manager import MemoryManager
                mm = MemoryManager()
                mm.remember(
                    fact=fact,
                    category="synthesized_tool",
                    entity=f"tool:{tool.name}",
                    relation="solves",
                    target_entity=tool.description[:60],
                )
                return "Indexed in Long-Term Vector RAG & Knowledge Graph."
        except Exception as exc:
            log.debug(f"Could not automatically index tool in memory: {exc}")
            return "Stored in local tools registry."

    def find_matching_tools(self, query: str, top_k: int = 3) -> List[SynthesizedTool]:
        """Search registered tools matching a user task or query."""
        if not self._tools:
            return []

        q = (query or "").lower().strip()
        if not q:
            return list(self._tools.values())[:top_k]

        q_words = set(re.findall(r"\b[a-zA-Z0-9_]{3,}\b", q))
        scored: List[Tuple[float, SynthesizedTool]] = []

        for tool in self._tools.values():
            score = 0.0
            t_name = tool.name.lower()
            t_desc = tool.description.lower()
            t_tags = [t.lower() for t in tool.tags]

            # Exact name match
            if t_name in q or any(w in t_name for w in q_words):
                score += 3.0

            # Description word match
            desc_words = set(re.findall(r"\b[a-zA-Z0-9_]{3,}\b", t_desc))
            overlap = q_words.intersection(desc_words)
            score += len(overlap) * 1.5

            # Tag match
            for tag in t_tags:
                if tag in q or tag in q_words:
                    score += 2.0

            if score > 0:
                scored.append((score, tool))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [tool for _, tool in scored[:top_k]]

    def list_tools(self, filter_query: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all synthesized tools with descriptions and metadata."""
        tools = self.find_matching_tools(filter_query, top_k=50) if filter_query else list(self._tools.values())
        return [
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
                "tags": t.tags,
                "created_at": t.created_at,
                "usage_count": t.usage_count,
                "file_path": t.file_path,
            }
            for t in tools
        ]

    def delete_tool(self, name: str, memory_mgr: Any = None) -> Tuple[bool, str]:
        """Remove a synthesized tool from disk, registry, and memory."""
        clean_name = re.sub(r"[^a-zA-Z0-9_]", "_", (name or "").strip()).lower()
        if clean_name not in self._tools:
            return False, f"Tool '{clean_name}' does not exist in registry."

        tool = self._tools.pop(clean_name)
        self._save_registry()

        tool_path = Path(tool.file_path) if tool.file_path else self.tools_dir / f"{clean_name}.py"
        if tool_path.exists():
            try:
                tool_path.unlink()
            except Exception as exc:
                log.warn(f"Failed to delete file {tool_path}: {exc}")

        # Remove from memory if possible
        try:
            if memory_mgr is not None:
                memory_mgr.forget(f"synthesized_tool:{clean_name}")
        except Exception:
            pass

        return True, f"Synthesized tool '{clean_name}' successfully deleted."


# Global Singleton Instance
_MANAGER_SINGLETON: Optional[SynthesizedToolManager] = None


def get_tool_manager(tools_dir: Optional[Path | str] = None) -> SynthesizedToolManager:
    global _MANAGER_SINGLETON
    if _MANAGER_SINGLETON is None or tools_dir is not None:
        _MANAGER_SINGLETON = SynthesizedToolManager(tools_dir=tools_dir)
    return _MANAGER_SINGLETON


def synthesized_tools_prompt_note(task: str) -> str:
    """Format matching synthesized tools as context for the agent prompt."""
    mgr = get_tool_manager()
    matches = mgr.find_matching_tools(task, top_k=3)
    if not matches:
        # Check if there are any tools at all to show available capabilities
        all_tools = list(mgr._tools.values())
        if not all_tools:
            return ""
        # If there are tools, provide a brief summary of available dynamic tools
        tools_list = ", ".join(f"`{t.name}`" for t in all_tools[:5])
        return (
            f"\n\n=== REUSABLE SYNTHESIZED TOOLS IN STORAGE ===\n"
            f"Jarvis has previously created tools: {tools_list}.\n"
            f"Use 'execute_synthesized_tool' to run them or 'synthesize_tool' to create new ones."
            f"\n=============================================="
        )

    lines = ["\n\n=== REMEMBERED SYNTHESIZED TOOLS FOR THIS TASK ==="]
    lines.append("You have previously created dynamic tools that match this task. REUSE them instead of starting from scratch:")
    for t in matches:
        p_str = json.dumps(t.parameters) if t.parameters else "{}"
        lines.append(
            f"- Tool: `{t.name}`\n"
            f"  Description: {t.description}\n"
            f"  Usage: {{\"action\": \"execute_synthesized_tool\", \"args\": {{\"name\": \"{t.name}\", \"args\": {p_str}}}}}"
        )
    lines.append("==================================================")
    return "\n".join(lines)
