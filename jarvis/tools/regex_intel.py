"""Regex Pattern Intelligence, Extraction & Redaction Engine for Jarvis.

Enables regex compilation testing, match extraction with character spans and named groups,
safe regex find-and-replace, and automated sensitive data/PII/secret redaction.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .files import _expand, _within

_REDACTION_PRESETS: Dict[str, Tuple[re.Pattern, str]] = {
    "email": (
        re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", re.IGNORECASE),
        "[REDACTED_EMAIL]",
    ),
    "ipv4": (
        re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"),
        "[REDACTED_IPV4]",
    ),
    "jwt": (
        re.compile(r"\beyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\b"),
        "[REDACTED_JWT]",
    ),
    "api_key": (
        re.compile(r"\b(?:sk-[a-zA-Z0-9]{20,}|ghp_[a-zA-Z0-9]{36}|AIza[0-9A-Za-z-_]{35}|AKIA[0-9A-Z]{16})\b"),
        "[REDACTED_API_KEY]",
    ),
    "credit_card": (
        re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b"),
        "[REDACTED_CREDIT_CARD]",
    ),
    "phone": (
        re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
        "[REDACTED_PHONE]",
    ),
}


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


def _parse_flags(flags_str: str) -> int:
    """Parse regex flag letters into int bitmask."""
    flags = 0
    clean = (flags_str or "").lower()
    if "i" in clean:
        flags |= re.IGNORECASE
    if "m" in clean:
        flags |= re.MULTILINE
    if "s" in clean:
        flags |= re.DOTALL
    if "x" in clean:
        flags |= re.VERBOSE
    if "a" in clean:
        flags |= re.ASCII
    return flags


# --------------------------------------------------------------------------- #
# Operations
# --------------------------------------------------------------------------- #

def test_pattern(pattern: str, text: str, flags_str: str = "") -> Dict[str, Any]:
    """Test regex pattern compilation and match against sample text."""
    flags = _parse_flags(flags_str)
    try:
        rx = re.compile(pattern, flags)
    except re.error as exc:
        return {
            "valid": False,
            "error": str(exc),
            "pattern": pattern,
            "matches": False,
        }

    match = rx.search(text)
    return {
        "valid": True,
        "pattern": pattern,
        "matches": match is not None,
        "match_text": match.group(0) if match else None,
        "match_span": list(match.span()) if match else None,
        "groups": list(match.groups()) if match else [],
        "named_groups": match.groupdict() if match else {},
    }


def extract_matches(
    pattern: str,
    text: str,
    flags_str: str = "",
    max_matches: int = 100,
) -> Dict[str, Any]:
    """Extract all regex matches with character spans and capture groups."""
    flags = _parse_flags(flags_str)
    try:
        rx = re.compile(pattern, flags)
    except re.error as exc:
        return {"error": f"Invalid regex pattern: {exc}"}

    matches: List[Dict[str, Any]] = []
    for idx, m in enumerate(rx.finditer(text)):
        if idx >= max_matches:
            break
        matches.append({
            "index": idx + 1,
            "match": m.group(0),
            "span": list(m.span()),
            "groups": list(m.groups()),
            "named_groups": m.groupdict(),
        })

    return {
        "pattern": pattern,
        "total_matches": len(matches),
        "matches": matches,
    }


def replace_pattern(
    pattern: str,
    text: str,
    replacement: str,
    flags_str: str = "",
) -> Dict[str, Any]:
    """Perform regex substitution."""
    flags = _parse_flags(flags_str)
    try:
        rx = re.compile(pattern, flags)
    except re.error as exc:
        return {"error": f"Invalid regex pattern: {exc}"}

    new_text, count = rx.subn(replacement, text)
    return {
        "pattern": pattern,
        "replacements_count": count,
        "result_text": new_text,
    }


def redact_text(text: str, preset: str = "all") -> Dict[str, Any]:
    """Redact sensitive PII and secret patterns from text."""
    target_presets = (
        list(_REDACTION_PRESETS.keys())
        if (not preset or preset.lower() == "all")
        else [p.strip().lower() for p in preset.split(",") if p.strip().lower() in _REDACTION_PRESETS]
    )

    current_text = text
    stats: Dict[str, int] = {}

    for name in target_presets:
        rx, mask = _REDACTION_PRESETS[name]
        current_text, count = rx.subn(mask, current_text)
        stats[name] = count

    total_redacted = sum(stats.values())
    return {
        "total_redactions": total_redacted,
        "redactions_by_type": stats,
        "redacted_text": current_text,
    }


# --------------------------------------------------------------------------- #
# Main Entrypoint
# --------------------------------------------------------------------------- #

def regex_intel(
    op: str = "extract",
    pattern: str = "",
    text: str = "",
    replacement: str = "",
    preset: str = "all",
    flags: str = "",
    allow: tuple[str, ...] = (),
) -> str:
    """Regex compilation testing, extraction, substitution, and PII redaction.

    Operations:
      - 'extract': Extract all matches, spans, and named groups.
      - 'test' / 'match': Verify regex syntax and check match against text.
      - 'replace': Substitute regex matches with replacement string.
      - 'redact': Automatically redact emails, IPs, API keys, cards, phone numbers.
    """
    op_clean = (op or "extract").strip().lower()
    raw_text, file_path = _read_content(text)

    if op_clean in ("test", "match", "validate"):
        if not pattern:
            return "regex_intel op='test' requires a 'pattern'"
        res = test_pattern(pattern=pattern, text=raw_text, flags_str=flags)
        return json.dumps(res, indent=2)

    elif op_clean in ("extract", "find", "findall", "search"):
        if not pattern:
            return "regex_intel op='extract' requires a 'pattern'"
        res = extract_matches(pattern=pattern, text=raw_text, flags_str=flags)
        return json.dumps(res, indent=2)

    elif op_clean in ("replace", "sub"):
        if not pattern:
            return "regex_intel op='replace' requires a 'pattern'"
        res = replace_pattern(pattern=pattern, text=raw_text, replacement=replacement, flags_str=flags)
        return json.dumps(res, indent=2)

    elif op_clean in ("redact", "mask", "sanitize"):
        res = redact_text(text=raw_text, preset=preset)
        return json.dumps(res, indent=2)

    return f"unknown regex_intel op '{op}' - supported: extract, test, replace, redact"
