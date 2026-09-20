"""Divergence report: declarations vs. handler argument handling.

Read-only. Compares what ``jarvis/tools/schema.py`` *declares* for each action
parameter against what the ``_h_*`` handler in ``jarvis/tools/registry.py``
*enforces*, and writes two artifacts next to this script:

* ``divergence.json``  — machine-readable baseline (commit-friendly worklist)
* ``divergence.html``  — human-readable table in the review-report style

Findings tracked per (action, param):

* ``undeclared-required-guard``   handler demands a param the declaration marks
                                  optional, or errors on missing even though
                                  ``required=True`` with no lenient default
* ``undeclared-clamp``            handler clamps to a numeric range the
                                  declaration does not express
* ``default-mismatch``            handler default value differs from declared
* ``undeclared-default``          handler supplies a default for an optional
                                  param, but the declaration leaves it ``None``
                                  (informational: the effective default)
* ``undeclared-coercion``         handler coerces the value differently than the
                                  declared type implies (e.g. declared ``str``
                                  but handler tolerates numbers / a str enum is
                                  silently re-fetched)
* ``handler-delegation``          handler passes ``args`` to a module-level
                                  helper (recorded once, per action, so "never
                                  reads it" findings are not emitted when the
                                  read happens inside the helper)
* ``whole-args-delegated``        handler passes the *entire* ``args`` object to
                                  another module (e.g. ``mcp.manage(args)``) —
                                  every declared param is enforced there, not
                                  in the handler

Everything is static analysis (``ast``) over the two modules; nothing imports
the handler code or changes any runtime behaviour.

Usage:
    python scripts/divergence_report.py            # write artifacts
    python scripts/divergence_report.py --summary  # console table only
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from jarvis.tools.schema import ACTIONS  # declarations (frozen dataclasses)

REGISTRY_PATH = REPO_ROOT / "jarvis" / "tools" / "registry.py"

FINDING_KINDS = (
    "undeclared-required-guard",
    "undeclared-clamp",
    "default-mismatch",
    "undeclared-default",
    "undeclared-coercion",
    "handler-delegation",
    "whole-args-delegated",
)


# --------------------------------------------------------------------------- #
# static extraction
# --------------------------------------------------------------------------- #

@dataclass
class Coercion:
    """One place a handler touches a single argument."""
    param: str
    kind: str            # "get" | "num" | "str" | "int" | "float" | "enum" | "required"
    default: object = None
    lo: float | None = None
    hi: float | None = None
    values: tuple = ()
    line: int = 0


@dataclass
class HandlerFacts:
    name: str
    coercions: list[Coercion] = field(default_factory=list)


def _lit(node: ast.AST):
    """Best-effort literal value of an AST node (constants and simple ops)."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        inner = _lit(node.operand)
        if isinstance(inner, (int, float)):
            return -inner if isinstance(node.op, ast.USub) else inner
    if isinstance(node, ast.List):
        vals = [_lit(e) for e in node.elts]
        return tuple(vals) if all(v is not None for v in vals) else None
    if isinstance(node, ast.Tuple):
        vals = [_lit(e) for e in node.elts]
        return tuple(vals) if all(v is not None for v in vals) else None
    if isinstance(node, ast.Attribute):  # e.g. SomeEnum.MEMBER -> "MEMBER"
        return node.attr
    return None


def _is_helper(call: ast.Call, names: set[str]) -> bool:
    fn = call.func
    return isinstance(fn, ast.Name) and fn.id in names


class HandlerVisitor(ast.NodeVisitor):
    """Collect how each ``_h_<action>`` handler touches ``args``.

    Handlers routinely delegate to module-level helpers that receive ``args``
    (``_resolve_point(args, …)``, ``_target_desc(args, …)``); the reads happen
    inside the helper's body.  On such a call the visitor recurses into the
    helper (with a recursion guard) and attributes the coercions it finds to
    the calling handler.
    """

    def __init__(self, helpers: dict[str, ast.FunctionDef] | None = None) -> None:
        self.facts: dict[str, HandlerFacts] = {}
        self._current: str | None = None
        self._tracing: set[str] = set()      # helper funcs being expanded
        self.helpers = helpers or {}
        # helper wrappers whose inner arg-name is positional or keyword
        self.helper_names = {"_num", "_bool", "_str", "_int", "_float", "_list",
                             "_opt_str", "_opt_int", "_req_str", "_req_int"}

    # -- track which handler we are inside of --------------------------------
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        prev, self._current = self._current, None
        if node.name.startswith("_h_"):
            self._current = node.name[len("_h_"):]
            self.facts.setdefault(self._current, HandlerFacts(self._current))
        self.generic_visit(node)
        self._current = prev

    # Python 3.8 compat guard: AsyncFunctionDef not expected here
    visit_AsyncFunctionDef = visit_FunctionDef

    def _add(self, **kwargs) -> None:
        if self._current is None:
            return
        self.facts[self._current].coercions.append(Coercion(**kwargs))

    # -- the shapes we understand --------------------------------------------
    def visit_Call(self, node: ast.Call) -> None:
        # 0. delegation:  _some_helper(args, ...) — trace into its body
        #    (known arg-name wrappers like _num are NOT delegation; branch 1
        #    handles them)
        if (isinstance(node.func, ast.Name) and node.func.id in self.helpers
                and node.func.id not in self.helper_names
                and node.func.id not in self._tracing
                and not node.func.id.startswith("_h_")
                and node.args
                and isinstance(node.args[0], ast.Name) and node.args[0].id == "args"):
            helper = self.helpers[node.func.id]
            helper_params = [a.arg for a in helper.args.args]
            if helper_params and helper_params[0] == "args":
                self._add(param="<args>", kind="delegation",
                          default=node.func.id, line=node.lineno)
                self._tracing.add(node.func.id)
                for stmt in helper.body:
                    self.generic_visit(stmt)
                self._tracing.discard(node.func.id)
                return

        # 1. helper wrappers:  _num(args, "key", default, lo, hi)
        if _is_helper(node, self.helper_names) and node.args:
            first = node.args[0]
            key = _lit(node.args[1]) if len(node.args) > 1 else None
            if isinstance(first, ast.Name) and first.id == "args" and isinstance(key, str):
                self._add(param=key, kind="num",
                          default=_lit(node.args[2]) if len(node.args) > 2 else None,
                          lo=_lit(node.args[3]) if len(node.args) > 3 else None,
                          hi=_lit(node.args[4]) if len(node.args) > 4 else None,
                          line=node.lineno)
                self.generic_visit(node)
                return

        # 2. coercion wrappers:  str(args.get("key"[, default])) etc.
        kind = _coercion_wrapper_kind(node)
        if kind:
            inner = node.args[0]
            key = _lit(inner.args[0]) if isinstance(inner, ast.Call) and inner.args else None
            if isinstance(key, str):
                has_dflt = len(inner.args) > 1
                self._add(param=key, kind=kind,
                          default=_lit(inner.args[1]) if has_dflt else "<MISSING>",
                          line=node.lineno)
                # NOTE: no early return — the fallback slot may itself read
                # args (e.g. args.get("text", args.get("script", "")));
                # duplicates are removed by the report-level dedupe.

        # 2b. whole-args pass to another module:  mod.fn(args, ...)
        #     (Attribute func ⇒ not a local helper; receiver may be any name)
        if (isinstance(node.func, ast.Attribute) and node.args
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id == "args"):
            owner = node.func.value.id if isinstance(node.func.value, ast.Name) else "?"
            self._add(param="<args>", kind="external",
                      default=f"{owner}.{node.func.attr}", line=node.lineno)
            self.generic_visit(node)
            return

        # 3. direct read:  args.get("key"[, default]) / args["key"] handled in
        #    visit_Subscript
        if (isinstance(node.func, ast.Attribute) and node.func.attr == "get"):
            recv = node.func.value
            if isinstance(recv, ast.Name) and recv.id == "args" and node.args:
                key = _lit(node.args[0])
                if isinstance(key, str):
                    default = _lit(node.args[1]) if len(node.args) > 1 else None
                    self._add(param=key, kind="get",
                              default=default if len(node.args) > 1 else "<MISSING>",
                              line=node.lineno)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        # args["key"] — required-style access; a KeyError/own guard follows
        if isinstance(node.value, ast.Name) and node.value.id == "args":
            key = _lit(node.slice)
            if isinstance(key, str):
                self._add(param=key, kind="required", line=node.lineno)
        self.generic_visit(node)


def _coercion_wrapper_kind(node: ast.Call) -> str | None:
    """Return 'str'/'int'/'float' when the call is str(args.get(...)) etc."""
    if isinstance(node.func, ast.Name) and node.func.id in ("str", "int", "float"):
        for arg in node.args:
            if (isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute)
                    and arg.func.attr == "get"
                    and isinstance(arg.func.value, ast.Name)
                    and arg.func.value.id == "args"):
                return node.func.id
    return None


def extract_handler_facts(source: str) -> dict[str, HandlerFacts]:
    tree = ast.parse(source)
    helpers = {
        node.name: node for node in tree.body
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_h_")
    }
    visitor = HandlerVisitor(helpers=helpers)
    visitor.visit(tree)
    return visitor.facts


# --------------------------------------------------------------------------- #
# comparison
# --------------------------------------------------------------------------- #

def _norm(v) -> str:
    return json.dumps(v, sort_keys=True, default=str)


def compare(declarations, handlers: dict[str, HandlerFacts]):
    """Yield finding dicts."""
    for action in declarations:
        hf = handlers.get(action.name)
        declared = {p.name: p for p in action.params}
        touched: set[str] = set()

        if hf:
            grouped: dict[str, list[Coercion]] = defaultdict(list)
            for c in hf.coercions:
                if c.kind in ("delegation", "external"):
                    continue  # tracing markers, not specific param reads
                grouped[c.param].append(c)
            touched = set(grouped)

            for param, cs in grouped.items():
                decl = declared.get(param)

                # ---- handler enforces but declaration calls optional ----
                required_access = any(c.kind == "required" for c in cs)
                if decl is None:
                    if required_access:
                        yield _f(action.name, param, "undeclared-required-guard",
                                 f"handler reads args[{param!r}] (required-style) "
                                 f"but no such param is declared")
                    else:
                        yield _f(action.name, param, "undeclared-coercion",
                                 f"handler touches arg {param!r} but it is not declared")
                    continue

                if required_access and not decl.required:
                    yield _f(action.name, param, "undeclared-required-guard",
                             f"declared optional (required=False) but handler "
                             f"reads args[{param!r}] hard")

                # ---- clamps ----
                nums = [c for c in cs if c.kind == "num" and c.lo is not None]
                declared_bounds = (decl.minimum, decl.maximum)
                if nums and decl.type not in ("int", "float"):
                    yield _f(action.name, param, "undeclared-clamp",
                             f"handler clamps to [{nums[0].lo}, {nums[0].hi}] "
                             f"but declared type is {decl.type!r}")
                for c in nums:
                    if (c.lo, c.hi) == declared_bounds:
                        continue  # handler enforces exactly the declared range
                    if decl.minimum is None and decl.maximum is None:
                        yield _f(action.name, param, "undeclared-clamp",
                                 f"clamped to [{c.lo}, {c.hi}] — not expressed in "
                                 f"the declaration", line=c.line)
                    else:
                        yield _f(action.name, param, "default-mismatch",
                                 f"handler clamps to [{c.lo}, {c.hi}] but "
                                 f"declaration says [{decl.minimum}, {decl.maximum}]",
                                 line=c.line)

                # ---- defaults ----
                dflts = [c for c in cs if c.kind != "required" and c.default is not None
                         and c.default != "<MISSING>"]
                if dflts:
                    eff = dflts[0].default
                    if decl.default is not None and _norm(eff) != _norm(decl.default):
                        yield _f(action.name, param, "default-mismatch",
                                 f"handler default {eff!r} vs declared "
                                 f"{decl.default!r}")
                    elif decl.default is None and not decl.required:
                        yield _f(action.name, param, "undeclared-default",
                                 f"handler default {eff!r}, declaration "
                                 f"leaves default unset")

                # ---- declared str but coerced as number (or vice versa) ----
                coerce_kinds = {c.kind for c in cs if c.kind in ("str", "int", "float", "num")}
                if decl.type in ("int", "float") and "str" in coerce_kinds:
                    yield _f(action.name, param, "undeclared-coercion",
                             f"declared {decl.type} but handler coerces via str()")
                if decl.type == "str" and coerce_kinds & {"int", "float", "num"}:
                    yield _f(action.name, param, "undeclared-coercion",
                             f"declared str but handler coerces numerically")

        # ---- declared but never touched by the handler ----
        delegated = bool(hf and any(c.kind == "delegation" for c in hf.coercions))
        externals = [c.default for c in (hf.coercions if hf else [])
                     if c.kind == "external"]
        for pname, decl in declared.items():
            if pname not in touched:
                if delegated:
                    yield _f(action.name, pname, "handler-delegation",
                             f"not read in the handler body; the handler passes "
                             f"args to a helper, so it may be read there")
                elif externals:
                    yield _f(action.name, pname, "whole-args-delegated",
                             f"handler passes the whole args object to "
                             f"{', '.join(sorted(set(externals)))} — the declared "
                             f"params are enforced there, not here")
                else:
                    yield _f(action.name, pname, "undeclared-coercion",
                             f"declared but the handler never reads it")


def _f(action, param, kind, detail, line=None):
    return {"action": action, "param": param, "kind": kind, "detail": detail,
            "line": line}


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def build_report() -> dict:
    source = REGISTRY_PATH.read_text(encoding="utf-8")
    handlers = extract_handler_facts(source)

    findings = list(compare(ACTIONS, handlers))
    # duplicate coercions can be recorded twice (direct read + read inside a
    # traced helper) — keep one row per distinct (action, param, kind, detail)
    seen: dict[tuple, dict] = {}
    for f in findings:
        key = (f["action"], f["param"], f["kind"], f["detail"])
        seen.setdefault(key, f)
    findings = list(seen.values())

    by_kind = defaultdict(int)
    by_action = defaultdict(int)
    for f in findings:
        by_kind[f["kind"]] += 1
        by_action[f["action"]] += 1

    return {
        "generated_by": "scripts/divergence_report.py (read-only static analysis)",
        "actions_declared": len(ACTIONS),
        "handlers_found": len(handlers),
        "total_findings": len(findings),
        "by_kind": dict(by_kind),
        "by_action": dict(by_action),
        "findings": findings,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--summary", action="store_true", help="console table only")
    args = ap.parse_args()

    report = build_report()

    if args.summary:
        for f in report["findings"]:
            print(f"{f['action']:>22}  {f['param']:<14} {f['kind']:<26} {f['detail']}")
        print(f"\n{report['total_findings']} findings across "
              f"{len(report['by_action'])} actions")
        return 0

    out_json = Path(__file__).parent / "divergence.json"
    out_html = Path(__file__).parent / "divergence.html"
    out_json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    # ---- HTML ----------------------------------------------------------------
    import html as html_mod
    esc = html_mod.escape
    rows = []
    for f in report["findings"]:
        color = {
            "undeclared-required-guard": "#f85149",
            "undeclared-clamp": "#d29922",
            "default-mismatch": "#f85149",
            "undeclared-default": "#8b949e",
            "undeclared-coercion": "#58a6ff",
            "handler-delegation": "#8b949e",
            "whole-args-delegated": "#bc8cff",
        }.get(f["kind"], "#8b949e")
        rows.append(
            f"<tr><td class='mono'>{esc(f['action'])}</td>"
            f"<td class='mono'>{esc(f['param'])}</td>"
            f"<td style='color:{color}' class='mono'>{esc(f['kind'])}</td>"
            f"<td>{esc(f['detail'])}</td></tr>"
        )

    html_text = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Jarvis — divergence report (declarations vs handlers)</title>
<style>
 body{{background:#0d1117;color:#e6edf3;font-family:-apple-system,'Segoe UI',Roboto,sans-serif;
      margin:0;padding:32px;line-height:1.55}}
 h1{{font-size:22px}} .sub{{color:#8b949e;font-size:14px;margin-bottom:20px}}
 table{{border-collapse:collapse;width:100%;font-size:13.5px}}
 th{{text-align:left;font-family:ui-monospace,monospace;font-size:11px;letter-spacing:.08em;
    text-transform:uppercase;color:#8b949e;padding:8px 12px;border-bottom:1px solid #30363d}}
 td{{padding:7px 12px;border-bottom:1px solid #21262d;vertical-align:top}}
 .mono{{font-family:ui-monospace,monospace;font-size:12.5px}}
 .stat{{display:inline-block;background:#161b22;border:1px solid #30363d;border-radius:8px;
       padding:6px 14px;margin-right:10px;font-size:13px}}
 .stat b{{font-family:ui-monospace,monospace}}
</style></head><body>
<h1>Divergence report — declarations vs. handler enforcement</h1>
<p class="sub">Read-only static analysis of jarvis/tools/registry.py against jarvis/tools/schema.py.
Regenerate with <code>python scripts/divergence_report.py</code>.</p>
<p>
 <span class="stat">actions <b>{report['actions_declared']}</b></span>
 <span class="stat">findings <b>{report['total_findings']}</b></span>
 <span class="stat">actions touched <b>{len(report['by_action'])}</b></span>
</p>
<table>
<thead><tr><th>action</th><th>param</th><th>kind</th><th>detail</th></tr></thead>
<tbody>
{''.join(rows)}
</tbody></table>
</body></html>"""
    out_html.write_text(html_text, encoding="utf-8")

    print(f"wrote {out_json.name} and {out_html.name}: "
          f"{report['total_findings']} findings across "
          f"{len(report['by_action'])} of {report['actions_declared']} actions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
