"""Unit and integration tests for Cryptographic, Hash & Secret Intelligence Engine."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import pytest

from jarvis.config import load_config
from jarvis.perception.elements import Observation
from jarvis.tools import crypto_intel, registry
from jarvis.tools.schema import ACTIONS_BY_NAME


@pytest.fixture
def sample_test_file():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "data.bin"
        # Write 128KB of known content
        p.write_bytes(b"A" * 131072)
        yield p


def test_schema_and_registry_registration():
    """Verify crypto_intel is in schema and registered in handlers."""
    assert "crypto_intel" in ACTIONS_BY_NAME
    action = ACTIONS_BY_NAME["crypto_intel"]
    assert action.category == "security"
    assert any(p.name == "op" for p in action.params)
    assert any(p.name == "target" for p in action.params)
    assert any(p.name == "algo" for p in action.params)


def test_string_and_file_hashing(sample_test_file):
    """Test computing SHA-256 and MD5 on strings and files."""
    res_str = crypto_intel.crypto_intel(op="hash", target="hello world", algo="sha256")
    data_str = json.loads(res_str)
    assert data_str["hash"] == hashlib.sha256(b"hello world").hexdigest()

    res_file = crypto_intel.crypto_intel(op="hash", target=str(sample_test_file), algo="sha256")
    data_file = json.loads(res_file)
    assert data_file["bytes_hashed"] == 131072
    assert data_file["hash"] == hashlib.sha256(b"A" * 131072).hexdigest()


def test_hash_verification(sample_test_file):
    """Test constant-time hash verification."""
    expected_hash = hashlib.sha256(b"A" * 131072).hexdigest()
    res_valid = crypto_intel.crypto_intel(
        op="verify",
        target=str(sample_test_file),
        expected=expected_hash,
        algo="sha256",
    )
    assert json.loads(res_valid)["verified"] is True

    res_invalid = crypto_intel.crypto_intel(
        op="verify",
        target=str(sample_test_file),
        expected="badbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadb",
        algo="sha256",
    )
    assert json.loads(res_invalid)["verified"] is False


def test_hmac_signature():
    """Test HMAC-SHA256 signature generation."""
    res = crypto_intel.crypto_intel(
        op="hmac",
        target="event=user_login&uid=123",
        key="secret_key_999",
        algo="sha256",
    )
    data = json.loads(res)
    assert data["algorithm"] == "HMAC-SHA256"
    assert len(data["signature"]) == 64


def test_token_and_secret_generation():
    """Test generating hex tokens, passwords, and UUID4s."""
    res_hex = crypto_intel.crypto_intel(op="token", target="hex", length=32)
    assert len(json.loads(res_hex)["token"]) == 32

    res_uuid = crypto_intel.crypto_intel(op="token", target="uuid")
    assert len(json.loads(res_uuid)["token"]) == 36

    res_pass = crypto_intel.crypto_intel(op="token", target="password", length=24)
    assert len(json.loads(res_pass)["token"]) == 24


def test_pbkdf2_derivation():
    """Test PBKDF2 password derivation."""
    res = crypto_intel.crypto_intel(
        op="pbkdf2",
        target="SuperSecretPassword!",
        salt="custom_salt_123",
        iterations=5000,
        algo="sha256",
    )
    data = json.loads(res)
    assert data["algorithm"] == "PBKDF2-HMAC-SHA256"
    assert len(data["derived_key_hex"]) == 64


def test_registry_execution():
    """Test executing crypto_intel through registry dispatcher."""
    cfg = load_config()
    obs = Observation(
        screenshot_path=None,
        elements=[],
        screen_size=(1920, 1080),
        active_window="Crypto",
    )

    res = registry.execute(
        name="crypto_intel",
        args={"op": "hash", "target": "test text", "algo": "sha256"},
        obs=obs,
        cfg=cfg,
    )
    assert res.ok is True
    assert '"algorithm": "sha256"' in res.message
    assert res.needs_observe is False
