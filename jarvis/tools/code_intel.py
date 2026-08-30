"""Codebase AST and Semantic Symbol Intelligence Engine for Jarvis.

Provides deep AST-level parsing, symbol extraction (classes, methods, functions,
arguments, type annotations, docstrings), cross-project symbol search, dependency
mapping, and architectural summaries without heavy external dependencies.
"""

from __future__ import annotations

import ast
import fnmatch
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .files import _expand, _within


_CODE_EXTS = {".py", ".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs", ".html", ".css", ".json", ".yaml", ".yml", ".sql"}
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".pytest_cache", ".mypy_cache", "dist", "build", ".next", ".nuxt"}


# --------------------------------------------------------------------------- #
# Python AST Symbol Extractor
# --------------------------------------------------------------------------- #

def _format_type_annotation(node: Optional[ast.AST]) -> str:
    """Convert AST type annotation node to human-readable string."""
    if node is None:
        return ""
    try:
        return ast.unparse(node)  # Python 3.9+ built-in
    except Exception:
        return ""


def _extract_py_args(args_node: ast.arguments) -> List[str]:
    """Format function parameter signature with types and defaults."""
    arg_list: List[str] = []
    defaults_offset = len(args_node.args) - len(args_node.defaults)

    for i, arg in enumerate(args_node.args):
        a_str = arg.arg
        if arg.annotation:
            ann = _format_type_annotation(arg.annotation)
            if ann:
                a_str += f": {ann}"
        # Check default
        def_idx = i - defaults_offset
        if def_idx >= 0 and def_idx < len(args_node.defaults):
            try:
                def_val = ast.unparse(args_node.defaults[def_idx])
                a_str += f" = {def_val}"
            except Exception:
                pass
        arg_list.append(a_str)

    if args_node.vararg:
        var_str = f"*{args_node.vararg.arg}"
        if args_node.vararg.annotation:
            ann = _format_type_annotation(args_node.vararg.annotation)
            if ann:
                var_str += f": {ann}"
        arg_list.append(var_str)

    if args_node.kwarg:
        kw_str = f"**{args_node.kwarg.arg}"
        if args_node.kwarg.annotation:
            ann = _format_type_annotation(args_node.kwarg.annotation)
            if ann:
                kw_str += f": {ann}"
        arg_list.append(kw_str)

    return arg_list


def parse_python_file(path: Path) -> Dict[str, Any]:
    """Extract structured symbols, imports, and docstrings from a Python source file."""
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(path))
    except Exception as exc:
        return {"file": str(path), "error": f"Failed to parse AST: {exc}"}

    module_doc = ast.get_docstring(tree) or ""
    classes: List[Dict[str, Any]] = []
    functions: List[Dict[str, Any]] = []
    imports: List[str] = []

    for node in tree.body:
        # Imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name + (f" as {alias.asname}" if alias.asname else "")
                imports.append(f"import {name}")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            dots = "." * node.level
            names = ", ".join(a.name + (f" as {a.asname}" if a.asname else "") for a in node.names)
            imports.append(f"from {dots}{mod} import {names}")

        # Top-level Functions
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            ret_type = _format_type_annotation(node.returns)
            functions.append({
                "name": node.name,
                "async": isinstance(node, ast.AsyncFunctionDef),
                "args": _extract_py_args(node.args),
                "return_type": ret_type,
                "docstring": (ast.get_docstring(node) or "").strip(),
                "start_line": node.lineno,
                "end_line": getattr(node, "end_lineno", node.lineno),
            })

        # Classes
        elif isinstance(node, ast.ClassDef):
            bases = [_format_type_annotation(b) for b in node.bases]
            methods: List[Dict[str, Any]] = []
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    ret_type = _format_type_annotation(item.returns)
                    methods.append({
                        "name": item.name,
                        "async": isinstance(item, ast.AsyncFunctionDef),
                        "args": _extract_py_args(item.args),
                        "return_type": ret_type,
                        "docstring": (ast.get_docstring(item) or "").strip(),
                        "start_line": item.lineno,
                        "end_line": getattr(item, "end_lineno", item.lineno),
                    })
            classes.append({
                "name": node.name,
                "bases": [b for b in bases if b],
                "docstring": (ast.get_docstring(node) or "").strip(),
                "start_line": node.lineno,
                "end_line": getattr(node, "end_lineno", node.lineno),
                "methods": methods,
            })

    return {
        "file": str(path),
        "docstring": module_doc.strip(),
        "classes": classes,
        "functions": functions,
        "imports": imports,
    }


# --------------------------------------------------------------------------- #
# JavaScript / TypeScript Symbol Extractor
# --------------------------------------------------------------------------- #

def parse_js_ts_file(path: Path) -> Dict[str, Any]:
    """Extract symbol signatures from JS/TS source files via regex."""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return {"file": str(path), "error": f"Failed to read file: {exc}"}

    lines = content.splitlines()
    classes: List[Dict[str, Any]] = []
    functions: List[Dict[str, Any]] = []
    imports: List[str] = []

    for lineno, line in enumerate(lines, 1):
        sline = line.strip()
        # Imports
        if sline.startswith("import ") or sline.startswith("const ") and "require(" in sline:
            imports.append(sline)
            continue

        # Classes: class Foo extends Bar {
        m_cls = re.search(r"class\s+([A-Za-z0-9_$]+)(?:\s+extends\s+([A-Za-z0-9_$]+))?", sline)
        if m_cls:
            name = m_cls.group(1)
            base = m_cls.group(2)
            classes.append({
                "name": name,
                "bases": [base] if base else [],
                "start_line": lineno,
                "methods": [],
            })
            continue

        # Functions: function foo(a, b), async function foo(), const foo = (...) =>
        m_fn = re.search(r"(?:export\s+)?(?:async\s+)?function\s+([A-Za-z0-9_$]+)\s*\(([^)]*)\)", sline)
        if m_fn:
            functions.append({
                "name": m_fn.group(1),
                "async": "async" in sline,
                "args": [a.strip() for a in m_fn.group(2).split(",") if a.strip()],
                "start_line": lineno,
            })
            continue

        # Arrow functions: const foo = async (...) =>
        m_arrow = re.search(r"(?:export\s+)?const\s+([A-Za-z0-9_$]+)\s*=\s*(?:async\s*)?\(([^)]*)\)\s*=>", sline)
        if m_arrow:
            functions.append({
                "name": m_arrow.group(1),
                "async": "async" in sline,
                "args": [a.strip() for a in m_arrow.group(2).split(",") if a.strip()],
                "start_line": lineno,
            })

    return {
        "file": str(path),
        "classes": classes,
        "functions": functions,
        "imports": imports[:20],
    }


# --------------------------------------------------------------------------- #
# Operations Dispatcher
# --------------------------------------------------------------------------- #

def get_symbols(path: Path) -> str:
    """Format all symbol definitions for a file or directory."""
    if path.is_file():
        ext = path.suffix.lower()
        data = parse_python_file(path) if ext == ".py" else parse_js_ts_file(path)
        if "error" in data:
            return data["error"]

        lines = [f"File: {path.name}"]
        if data.get("docstring"):
            lines.append(f'"""\n{data["docstring"]}\n"""')

        if data.get("classes"):
            lines.append("\nClasses:")
            for cls in data["classes"]:
                bases_str = f"({', '.join(cls['bases'])})" if cls.get("bases") else ""
                lines.append(f"  • class {cls['name']}{bases_str}  [line {cls['start_line']}]")
                if cls.get("docstring"):
                    lines.append(f"      doc: {cls['docstring'][:80]}")
                for m in cls.get("methods", []):
                    async_str = "async " if m.get("async") else ""
                    args_str = ", ".join(m.get("args", []))
                    ret_str = f" -> {m['return_type']}" if m.get("return_type") else ""
                    lines.append(f"      - {async_str}{m['name']}({args_str}){ret_str} [line {m['start_line']}]")

        if data.get("functions"):
            lines.append("\nFunctions:")
            for fn in data["functions"]:
                async_str = "async " if fn.get("async") else ""
                args_str = ", ".join(fn.get("args", []))
                ret_str = f" -> {fn['return_type']}" if fn.get("return_type") else ""
                lines.append(f"  • {async_str}{fn['name']}({args_str}){ret_str}  [line {fn['start_line']}]")
                if fn.get("docstring"):
                    lines.append(f"      doc: {fn['docstring'][:80]}")

        return "\n".join(lines)

    elif path.is_dir():
        summary_parts = []
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d.lower() not in _SKIP_DIRS]
            for f in sorted(files):
                p = Path(root) / f
                if p.suffix.lower() in (".py", ".js", ".ts"):
                    rel = p.relative_to(path)
                    data = parse_python_file(p) if p.suffix.lower() == ".py" else parse_js_ts_file(p)
                    cls_count = len(data.get("classes", []))
                    fn_count = len(data.get("functions", []))
                    cls_names = ", ".join(c["name"] for c in data.get("classes", [])[:4])
                    fn_names = ", ".join(fn["name"] for fn in data.get("functions", [])[:4])
                    items = []
                    if cls_names:
                        items.append(f"classes: {cls_names}")
                    if fn_names:
                        items.append(f"functions: {fn_names}")
                    details = f" ({'; '.join(items)})" if items else ""
                    summary_parts.append(f"• {rel}: {cls_count} classes, {fn_count} functions{details}")

        if not summary_parts:
            return f"no code files found in {path}"
        return f"Symbols in {path.name} ({len(summary_parts)} files):\n" + "\n".join(summary_parts)

    return f"path not found: {path}"


def search_symbols(path: Path, query: str, max_results: int = 30) -> str:
    """Search for matching class and function names across a codebase."""
    q_low = query.strip().lower()
    if not q_low:
        return "search needs a query string"

    root_dir = path if path.is_dir() else path.parent
    hits: List[str] = []

    for root, dirs, files in os.walk(root_dir):
        dirs[:] = [d for d in dirs if d.lower() not in _SKIP_DIRS]
        for f in sorted(files):
            p = Path(root) / f
            if p.suffix.lower() in (".py", ".js", ".ts"):
                data = parse_python_file(p) if p.suffix.lower() == ".py" else parse_js_ts_file(p)
                rel = p.relative_to(root_dir)

                for cls in data.get("classes", []):
                    if q_low in cls["name"].lower():
                        hits.append(f"class {cls['name']} -> {rel}:{cls['start_line']}")
                    for m in cls.get("methods", []):
                        if q_low in m["name"].lower():
                            hits.append(f"method {cls['name']}.{m['name']}() -> {rel}:{m['start_line']}")

                for fn in data.get("functions", []):
                    if q_low in fn["name"].lower():
                        hits.append(f"function {fn['name']}() -> {rel}:{fn['start_line']}")

                if len(hits) >= max_results:
                    break

    if not hits:
        return f"no symbols matching '{query}' found under {root_dir.name}"
    return f"found {len(hits)} matching symbol(s) for '{query}':\n" + "\n".join(f"  • {h}" for h in hits)


def analyze_dependencies(path: Path) -> str:
    """Map external dependencies and internal module imports across a project."""
    root_dir = path if path.is_dir() else path.parent
    external_pkgs: Set[str] = set()
    internal_imports: List[str] = []

    for root, dirs, files in os.walk(root_dir):
        dirs[:] = [d for d in dirs if d.lower() not in _SKIP_DIRS]
        for f in files:
            p = Path(root) / f
            if p.suffix.lower() == ".py":
                data = parse_python_file(p)
                for imp in data.get("imports", []):
                    # Classify import
                    parts = imp.split()
                    if len(parts) >= 2:
                        mod = parts[1].split(".")[0].strip(",")
                        if not mod.startswith("."):
                            external_pkgs.add(mod)
                        else:
                            internal_imports.append(f"{p.name} -> {imp}")

    ext_sorted = sorted(external_pkgs)
    lines = [f"Dependency Analysis for {root_dir.name}:"]
    lines.append(f"\nImported Packages ({len(ext_sorted)}):")
    lines.append("  " + ", ".join(ext_sorted[:40]))
    if internal_imports:
        lines.append(f"\nInternal Relative Imports ({len(internal_imports)} found):")
        lines.append("\n".join(f"  • {i}" for i in internal_imports[:15]))

    return "\n".join(lines)


def summarize_codebase(path: Path, max_depth: int = 3) -> str:
    """Generate a clean, token-efficient structural tree with symbol counts."""
    root_dir = path if path.is_dir() else path.parent
    lines = [f"Codebase Structure: {root_dir.name}/"]

    for root, dirs, files in os.walk(root_dir):
        dirs[:] = [d for d in dirs if d.lower() not in _SKIP_DIRS]
        rel = Path(root).relative_to(root_dir)
        depth = len(rel.parts)
        if depth > max_depth:
            continue

        indent = "  " * depth
        if str(rel) != ".":
            lines.append(f"{indent}📁 {rel.name}/")

        sub_indent = "  " * (depth + 1)
        code_files = [f for f in sorted(files) if Path(f).suffix.lower() in _CODE_EXTS]
        for cf in code_files:
            p = Path(root) / cf
            info = ""
            if p.suffix.lower() == ".py":
                d = parse_python_file(p)
                c_cnt = len(d.get("classes", []))
                f_cnt = len(d.get("functions", []))
                info = f" ({c_cnt}c, {f_cnt}f)" if (c_cnt or f_cnt) else ""
            lines.append(f"{sub_indent}📄 {cf}{info}")

    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Main Entrypoint
# --------------------------------------------------------------------------- #

def code_intel(
    op: str = "symbols",
    path: str = ".",
    query: str = "",
    max_depth: int = 3,
    allow: tuple[str, ...] = (),
) -> str:
    """Perform AST parsing, symbol search, dependency mapping, or architectural summaries.

    Operations:
      - 'symbols': Extract detailed classes, functions, arguments, docstrings from a file or folder.
      - 'search': Search for symbol definitions (classes/functions) by name.
      - 'dependencies': Extract all third-party and internal imports.
      - 'summary': High-level architectural map of directory tree with symbol counts.
    """
    p = _expand(path or ".")
    if not p.exists():
        return f"path not found: {p}"

    op_clean = (op or "symbols").strip().lower()

    if op_clean in ("symbols", "classes", "functions"):
        return get_symbols(p)
    elif op_clean in ("search", "find", "locate"):
        return search_symbols(p, query=query or path)
    elif op_clean in ("dependencies", "deps", "imports"):
        return analyze_dependencies(p)
    elif op_clean in ("summary", "overview", "structure", "tree"):
        return summarize_codebase(p, max_depth=max_depth)

    return f"unknown code_intel op '{op}' - supported: symbols, search, dependencies, summary"
