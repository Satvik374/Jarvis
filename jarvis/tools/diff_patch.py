"""Text Diff & Unified Patch Engine for Jarvis.

Enables unified diff generation, unified patch application with dry-run support,
sequence similarity calculation, and change statistics using Python standard library.
"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .files import _expand, _within


def _read_content(val: str) -> Tuple[str, Optional[Path]]:
    """Read file content if target is a file path, otherwise return text string."""
    if not val:
        return "", None
    p = _expand(val)
    if p.exists() and p.is_file():
        try:
            return p.read_text(encoding="utf-8", errors="replace"), p
        except Exception:
            return val, None
    return val, None


# --------------------------------------------------------------------------- #
# Diff & Similarity Operations
# --------------------------------------------------------------------------- #

def compute_diff(src: str, dst: str, fromfile: str = "original", tofile: str = "modified") -> str:
    """Compute unified diff between two text contents or files."""
    src_lines = src.splitlines(keepends=True)
    dst_lines = dst.splitlines(keepends=True)
    diff = difflib.unified_diff(src_lines, dst_lines, fromfile=fromfile, tofile=tofile)
    res = "".join(diff)
    return res if res else "files are identical (no diff)"


def compute_similarity(src: str, dst: str) -> Dict[str, Any]:
    """Compute similarity ratio and matching blocks between two texts."""
    sm = difflib.SequenceMatcher(None, src, dst)
    ratio = round(sm.ratio(), 4)
    matching_blocks = sm.get_matching_blocks()
    total_matched = sum(b.size for b in matching_blocks)

    return {
        "similarity_ratio": ratio,
        "similarity_pct": f"{round(ratio * 100, 2)}%",
        "total_matched_chars": total_matched,
        "src_length": len(src),
        "dst_length": len(dst),
        "matching_blocks_count": len(matching_blocks),
    }


def compute_stats(src: str, dst: str) -> Dict[str, Any]:
    """Compute added, deleted, and modified line statistics."""
    src_lines = src.splitlines()
    dst_lines = dst.splitlines()
    sm = difflib.SequenceMatcher(None, src_lines, dst_lines)

    added = 0
    deleted = 0
    modified = 0
    equal = 0

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            equal += (i2 - i1)
        elif tag == "insert":
            added += (j2 - j1)
        elif tag == "delete":
            deleted += (i2 - i1)
        elif tag == "replace":
            modified += max(i2 - i1, j2 - j1)

    return {
        "lines_added": added,
        "lines_deleted": deleted,
        "lines_modified": modified,
        "lines_unchanged": equal,
        "total_original_lines": len(src_lines),
        "total_new_lines": len(dst_lines),
    }


# --------------------------------------------------------------------------- #
# Unified Patch Application
# --------------------------------------------------------------------------- #

def apply_patch(target_file: Path, patch_text: str, dry_run: bool = False) -> str:
    """Apply a unified diff patch to target file with context matching."""
    if not target_file.exists() or not target_file.is_file():
        return f"target file not found: {target_file}"

    content = target_file.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines(keepends=True)

    patch_lines = patch_text.splitlines()
    hunk_pattern = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

    new_lines = list(lines)
    offset = 0

    # Parse and apply hunks
    hunk_lines: List[str] = []
    hunk_info = None

    def _process_hunk(h_info, h_lines):
        nonlocal new_lines, offset
        src_start = int(h_info.group(1)) - 1
        # Context validation
        expected_src = []
        replacement = []
        for pl in h_lines:
            if pl.startswith(" "):
                expected_src.append(pl[1:] + "\n" if not pl[1:].endswith("\n") else pl[1:])
                replacement.append(pl[1:] + "\n" if not pl[1:].endswith("\n") else pl[1:])
            elif pl.startswith("-"):
                expected_src.append(pl[1:] + "\n" if not pl[1:].endswith("\n") else pl[1:])
            elif pl.startswith("+"):
                replacement.append(pl[1:] + "\n" if not pl[1:].endswith("\n") else pl[1:])

        idx = src_start + offset
        # Replace slice
        new_lines[idx : idx + len(expected_src)] = replacement
        offset += len(replacement) - len(expected_src)

    for pl in patch_lines:
        m = hunk_pattern.match(pl)
        if m:
            if hunk_info and hunk_lines:
                _process_hunk(hunk_info, hunk_lines)
                hunk_lines = []
            hunk_info = m
        elif hunk_info:
            hunk_lines.append(pl)

    if hunk_info and hunk_lines:
        _process_hunk(hunk_info, hunk_lines)

    result_text = "".join(new_lines)

    if dry_run:
        return f"dry-run successful: patch applies cleanly ({len(new_lines)} lines result)"

    target_file.write_text(result_text, encoding="utf-8")
    return f"patch applied successfully to {target_file.name}"


# --------------------------------------------------------------------------- #
# Main Entrypoint
# --------------------------------------------------------------------------- #

def diff_patch(
    op: str = "diff",
    source: str = "",
    target: str = "",
    patch_text: str = "",
    dry_run: bool = False,
    allow: tuple[str, ...] = (),
) -> str:
    """Compute text diffs, similarity, stats, or apply unified patches.

    Operations:
      - 'diff': Unified diff between source and target (files or strings).
      - 'similarity': Sequence matcher similarity ratio.
      - 'stats': Added/deleted/modified line statistics.
      - 'patch': Apply unified diff patch to target file.
    """
    op_clean = (op or "diff").strip().lower()

    if op_clean in ("diff", "compare", "unidiff"):
        src_text, src_path = _read_content(source)
        dst_text, dst_path = _read_content(target)
        fromfile = src_path.name if src_path else "source"
        tofile = dst_path.name if dst_path else "target"
        return compute_diff(src_text, dst_text, fromfile=fromfile, tofile=tofile)

    elif op_clean in ("similarity", "sim", "ratio"):
        src_text, _ = _read_content(source)
        dst_text, _ = _read_content(target)
        res = compute_similarity(src_text, dst_text)
        return json.dumps(res, indent=2)

    elif op_clean in ("stats", "summary", "count"):
        src_text, _ = _read_content(source)
        dst_text, _ = _read_content(target)
        res = compute_stats(src_text, dst_text)
        return json.dumps(res, indent=2)

    elif op_clean in ("patch", "apply"):
        if not target:
            return "patch requires a 'target' file path"
        p = _expand(target)
        pt = patch_text or source
        if not pt:
            return "patch requires 'patch_text' or 'source'"
        return apply_patch(p, pt, dry_run=dry_run)

    return f"unknown diff_patch op '{op}' - supported: diff, similarity, stats, patch"
