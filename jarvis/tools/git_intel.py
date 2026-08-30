"""Git & Version Control Intelligence Engine for Jarvis.

Provides structured Git repository status, commit history, diff inspection,
branch management, staging, and commits without manual shell parsing.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .files import _expand, _within


def _run_git(args: List[str], cwd: Path, timeout: int = 20) -> Tuple[int, str, str]:
    """Execute git command in repository directory with UTF-8 encoding."""
    try:
        proc = subprocess.run(
            ["git"] + args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except FileNotFoundError:
        return -1, "", "git executable not found in system PATH"
    except Exception as exc:
        return -1, "", str(exc)


def get_git_status(repo: Path) -> str:
    """Introspect repository status, branch, staged and unstaged files."""
    code, out, err = _run_git(["status", "--porcelain=v1", "-b"], cwd=repo)
    if code != 0:
        return f"git error: {err or out}"

    lines = out.splitlines()
    if not lines:
        return "working directory clean, nothing to commit"

    branch_header = lines[0].replace("## ", "").strip()
    staged: List[str] = []
    unstaged: List[str] = []
    untracked: List[str] = []

    for line in lines[1:]:
        if len(line) < 3:
            continue
        staged_code = line[0]
        unstaged_code = line[1]
        filepath = line[3:].strip()

        if staged_code == "?" and unstaged_code == "?":
            untracked.append(filepath)
        else:
            if staged_code != " " and staged_code != "?":
                staged.append(f"[{staged_code}] {filepath}")
            if unstaged_code != " " and unstaged_code != "?":
                unstaged.append(f"[{unstaged_code}] {filepath}")

    res = [f"Git Repository Status ({repo.name}):"]
    res.append(f"  🌿 Branch: {branch_header}")

    if staged:
        res.append(f"\n  📦 Staged Changes ({len(staged)}):")
        for s in staged[:30]:
            res.append(f"    • {s}")
    if unstaged:
        res.append(f"\n  📝 Unstaged Changes ({len(unstaged)}):")
        for u in unstaged[:30]:
            res.append(f"    • {u}")
    if untracked:
        res.append(f"\n  ❓ Untracked Files ({len(untracked)}):")
        for ut in untracked[:30]:
            res.append(f"    • {ut}")

    if not staged and not unstaged and not untracked:
        res.append("\n  ✨ Working tree clean.")

    return "\n".join(res)


def get_git_log(repo: Path, limit: int = 10) -> str:
    """Format recent commit log."""
    lim = max(1, min(50, limit))
    code, out, err = _run_git(["log", f"-n{lim}", "--pretty=format:%h|%an|%ad|%s", "--date=short"], cwd=repo)
    if code != 0:
        return f"git log error: {err or out}"

    if not out.strip():
        return "no commits found in repository"

    lines = [f"Commit History for {repo.name} (last {lim} commits):"]
    for row in out.splitlines():
        parts = row.split("|", 3)
        if len(parts) == 4:
            commit_hash, author, date, subject = parts
            lines.append(f"  • [{commit_hash}] {date} by {author}: {subject}")
        else:
            lines.append(f"  • {row}")

    return "\n".join(lines)


def get_git_diff(repo: Path, staged: bool = False, target: str = "") -> str:
    """Get unified diff."""
    args = ["diff"]
    if staged:
        args.append("--cached")
    if target:
        args.extend(target.split())

    code, out, err = _run_git(args, cwd=repo)
    if code != 0:
        return f"git diff error: {err or out}"

    if not out.strip():
        return "no diff detected"
    return out[:10000]


def get_git_branches(repo: Path) -> str:
    """List local and remote branches."""
    code, out, err = _run_git(["branch", "-vv", "--all"], cwd=repo)
    if code != 0:
        return f"git branch error: {err or out}"

    if not out.strip():
        return "no branches found"

    lines = [f"Branches for {repo.name}:"]
    for b in out.splitlines():
        lines.append(f"  {b.strip()}")
    return "\n".join(lines)


def create_git_commit(repo: Path, message: str, stage_all: bool = True) -> str:
    """Stage changes and create a commit."""
    if not message.strip():
        return "commit requires a non-empty message"

    if stage_all:
        code_add, out_add, err_add = _run_git(["add", "-A"], cwd=repo)
        if code_add != 0:
            return f"git add failed: {err_add or out_add}"

    code_c, out_c, err_c = _run_git(["commit", "-m", message.strip()], cwd=repo)
    if code_c != 0:
        return f"git commit failed: {err_c or out_c}"

    return f"commit created: {out_c.splitlines()[0] if out_c else 'OK'}"


def git_intel(
    op: str = "status",
    path: str = ".",
    target: str = "",
    message: str = "",
    limit: int = 10,
    staged: bool = False,
    allow: tuple[str, ...] = (),
) -> str:
    """Inspect and operate on Git repositories.

    Operations:
      - 'status': Enumerate branch, staged, unstaged, and untracked files.
      - 'log': Formatted commit history.
      - 'diff': Unified diff of unstaged changes (or staged if staged=True).
      - 'branches': List local and remote branches.
      - 'commit': Stage and commit changes with a message.
    """
    p = _expand(path or ".")
    if not p.exists():
        return f"path not found: {p}"

    # Search upwards for .git directory if pointing inside subfolder
    repo_dir = p if p.is_dir() else p.parent
    found_git = False
    cur = repo_dir
    while cur != cur.parent:
        if (cur / ".git").exists():
            repo_dir = cur
            found_git = True
            break
        cur = cur.parent

    if not found_git:
        return f"'{p.name}' is not a git repository (no .git folder found)"

    op_clean = (op or "status").strip().lower()

    if op_clean in ("status", "stat", "info"):
        return get_git_status(repo_dir)
    elif op_clean in ("log", "history", "commits"):
        return get_git_log(repo_dir, limit=limit)
    elif op_clean in ("diff", "changes"):
        return get_git_diff(repo_dir, staged=staged, target=target)
    elif op_clean in ("branches", "branch"):
        return get_git_branches(repo_dir)
    elif op_clean in ("commit", "save"):
        return create_git_commit(repo_dir, message=message or target)

    return f"unknown git_intel op '{op}' - supported: status, log, diff, branches, commit"
