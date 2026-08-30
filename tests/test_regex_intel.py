"""Unit and integration tests for Regex Pattern Intelligence & Redaction Engine."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from jarvis.config import load_config
from jarvis.perception.elements import Observation
from jarvis.tools import regex_intel, registry
from jarvis.tools.schema import ACTIONS_BY_NAME


def test_schema_and_registry_registration():
    """Verify regex_intel is in schema and registered in handlers."""
    assert "regex_intel" in ACTIONS_BY_NAME
    action = ACTIONS_BY_NAME["regex_intel"]
    assert action.category == "coding"
    assert any(p.name == "op" for p in action.params)
    assert any(p.name == "pattern" for p in action.params)
    assert any(p.name == "text" for p in action.params)


def test_pattern_testing():
    """Test regex pattern compilation and matching."""
    res_valid = regex_intel.regex_intel(
        op="test",
        pattern=r"^v\d+\.\d+\.\d+$",
        text="v2.15.0",
    )
    data_v = json.loads(res_valid)
    assert data_v["valid"] is True
    assert data_v["matches"] is True
    assert data_v["match_text"] == "v2.15.0"

    res_invalid = regex_intel.regex_intel(
        op="test",
        pattern=r"([a-z+",  # unclosed parenthesis
        text="abc",
    )
    data_inv = json.loads(res_invalid)
    assert data_inv["valid"] is False
    assert "error" in data_inv


def test_extract_named_groups_and_spans():
    """Test extracting matches with named groups and character spans."""
    sample = "user=admin ip=192.168.1.1 role=super"
    pattern = r"(?P<key>\w+)=(?P<val>[^\s]+)"

    res = regex_intel.regex_intel(op="extract", pattern=pattern, text=sample)
    data = json.loads(res)

    assert data["total_matches"] == 3
    first_match = data["matches"][0]
    assert first_match["named_groups"]["key"] == "user"
    assert first_match["named_groups"]["val"] == "admin"
    assert first_match["span"] == [0, 10]


def test_replace_substitution():
    """Test regex substitution with backreferences."""
    sample = "Item: ABC-123 and XYZ-789"
    pattern = r"([A-Z]{3})-(\d{3})"
    replacement = r"Code(\1/\2)"

    res = regex_intel.regex_intel(
        op="replace",
        pattern=pattern,
        text=sample,
        replacement=replacement,
    )
    data = json.loads(res)
    assert data["replacements_count"] == 2
    assert data["result_text"] == "Item: Code(ABC/123) and Code(XYZ/789)"


def test_pii_and_secret_redaction():
    """Test automated redaction of sensitive credentials and PII."""
    raw = (
        "Contact dev@jarvis.ai or billing@example.com. "
        "Server IP 10.0.0.15, OpenAI key sk-12345678901234567890abcdef, "
        "and card 4111-2222-3333-4444."
    )

    res = regex_intel.regex_intel(op="redact", text=raw, preset="all")
    data = json.loads(res)

    assert data["total_redactions"] >= 4
    assert "[REDACTED_EMAIL]" in data["redacted_text"]
    assert "[REDACTED_IPV4]" in data["redacted_text"]
    assert "[REDACTED_API_KEY]" in data["redacted_text"]
    assert "[REDACTED_CREDIT_CARD]" in data["redacted_text"]
    assert "sk-1234567890" not in data["redacted_text"]


def test_extract_from_file(tmp_path):
    """Test reading and extracting matches directly from a file."""
    log_file = tmp_path / "server.log"
    log_file.write_text("ERROR [2026-08-18] db timeout\nINFO [2026-08-18] retry ok\n", encoding="utf-8")

    res = regex_intel.regex_intel(
        op="extract",
        pattern=r"\[(?P<date>\d{4}-\d{2}-\d{2})\] (?P<msg>.*)",
        text=str(log_file),
    )
    data = json.loads(res)
    assert data["total_matches"] == 2
    assert data["matches"][0]["named_groups"]["msg"] == "db timeout"


def test_registry_execution():
    """Test executing regex_intel through registry dispatcher."""
    cfg = load_config()
    obs = Observation(
        screenshot_path=None,
        elements=[],
        screen_size=(1920, 1080),
        active_window="Regex",
    )

    res = registry.execute(
        name="regex_intel",
        args={"op": "extract", "pattern": r"\d+", "text": "Jarvis 2026 v5"},
        obs=obs,
        cfg=cfg,
    )
    assert res.ok is True
    assert '"total_matches": 2' in res.message
    assert res.needs_observe is False
