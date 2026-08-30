"""Cryptographic, Hash & Secret Intelligence Engine for Jarvis.

Provides streaming file checksum calculation (SHA-256, MD5, SHA-512, BLAKE2),
constant-time hash verification, HMAC signatures, secure token/key generation,
and PBKDF2 password derivation without external dependencies.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import string
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .files import _expand, _within

_SUPPORTED_ALGOS = {
    "md5": hashlib.md5,
    "sha1": hashlib.sha1,
    "sha224": hashlib.sha224,
    "sha256": hashlib.sha256,
    "sha384": hashlib.sha384,
    "sha512": hashlib.sha512,
    "sha3_256": hashlib.sha3_256,
    "sha3_512": hashlib.sha3_512,
    "blake2b": hashlib.blake2b,
    "blake2s": hashlib.blake2s,
}


def _get_hash_obj(algo_name: str):
    clean = algo_name.lower().replace("-", "_").strip()
    if clean in _SUPPORTED_ALGOS:
        return _SUPPORTED_ALGOS[clean]()
    return hashlib.sha256()


# --------------------------------------------------------------------------- #
# Hashing and Checksums
# --------------------------------------------------------------------------- #

def compute_hash(target: str, algo: str = "sha256") -> Dict[str, Any]:
    """Compute hash of string or stream file chunks in 64KB blocks."""
    p = _expand(target)
    h = _get_hash_obj(algo)
    is_file = p.exists() and p.is_file()

    total_bytes = 0
    if is_file:
        with open(p, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
                total_bytes += len(chunk)
    else:
        raw = target.encode("utf-8")
        h.update(raw)
        total_bytes = len(raw)

    return {
        "algorithm": algo.lower(),
        "hash": h.hexdigest(),
        "target_type": "file" if is_file else "string",
        "target": str(p) if is_file else (target[:50] + "..." if len(target) > 50 else target),
        "bytes_hashed": total_bytes,
    }


def verify_hash(target: str, expected: str, algo: str = "sha256") -> Dict[str, Any]:
    """Constant-time verification of computed hash against expected checksum."""
    res = compute_hash(target, algo=algo)
    actual = res["hash"]
    exp_clean = expected.strip().lower()
    verified = hmac.compare_digest(actual, exp_clean)

    return {
        "verified": verified,
        "algorithm": algo.lower(),
        "actual_hash": actual,
        "expected_hash": exp_clean,
        "target": res["target"],
        "target_type": res["target_type"],
    }


# --------------------------------------------------------------------------- #
# HMAC & Key Derivation
# --------------------------------------------------------------------------- #

def compute_hmac(data: str, key: str, algo: str = "sha256") -> Dict[str, Any]:
    """Generate HMAC signature for webhook or message authentication."""
    algo_clean = algo.lower().replace("-", "_").strip()
    digestmod = getattr(hashlib, algo_clean, hashlib.sha256)
    sig = hmac.new(key.encode("utf-8"), data.encode("utf-8"), digestmod).hexdigest()

    return {
        "algorithm": f"HMAC-{algo_clean.upper()}",
        "signature": sig,
        "data_length": len(data),
    }


def derive_pbkdf2(password: str, salt: str = "", iterations: int = 100000, algo: str = "sha256") -> Dict[str, Any]:
    """Derive key using PBKDF2-HMAC."""
    s = salt if salt else secrets.token_hex(16)
    algo_clean = algo.lower().replace("-", "_").strip()
    derived = hashlib.pbkdf2_hmac(algo_clean, password.encode("utf-8"), s.encode("utf-8"), max(1000, iterations))

    return {
        "algorithm": f"PBKDF2-HMAC-{algo_clean.upper()}",
        "iterations": iterations,
        "salt": s,
        "derived_key_hex": derived.hex(),
    }


# --------------------------------------------------------------------------- #
# Token & Key Generation
# --------------------------------------------------------------------------- #

def generate_secret(token_type: str = "hex", length: int = 32) -> Dict[str, Any]:
    """Generate high-entropy cryptographically secure random token or key."""
    t_type = token_type.lower().strip()
    lim_len = max(8, min(256, length))

    if t_type in ("uuid", "uuid4"):
        val = str(uuid.uuid4())
    elif t_type in ("url", "urlsafe", "base64"):
        val = secrets.token_urlsafe(lim_len)[:lim_len]
    elif t_type in ("password", "pass"):
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
        val = "".join(secrets.choice(alphabet) for _ in range(lim_len))
    else:  # hex
        val = secrets.token_hex(lim_len // 2)

    return {
        "type": t_type,
        "token": val,
        "length": len(val),
    }


# --------------------------------------------------------------------------- #
# Main Entrypoint
# --------------------------------------------------------------------------- #

def crypto_intel(
    op: str = "hash",
    target: str = "",
    algo: str = "sha256",
    key: str = "",
    expected: str = "",
    length: int = 32,
    salt: str = "",
    iterations: int = 100000,
    allow: tuple[str, ...] = (),
) -> str:
    """Execute cryptographic hashing, checksum verification, HMAC, or token generation.

    Operations:
      - 'hash': Compute checksum (SHA-256, MD5, SHA-512, BLAKE2) of file or text.
      - 'verify': Verify file/text checksum against expected hash using constant-time comparison.
      - 'hmac': Compute HMAC signature with a secret key.
      - 'token' / 'keygen': Generate secure random tokens (hex, url, password, uuid4).
      - 'pbkdf2': Derive salted cryptographic key from password.
    """
    op_clean = (op or "hash").strip().lower()

    if op_clean in ("hash", "checksum", "digest"):
        if not target:
            return "crypto_intel 'hash' requires a 'target' (string or file path)"
        res = compute_hash(target, algo=algo)
        return json.dumps(res, indent=2)

    elif op_clean in ("verify", "check"):
        if not target or not expected:
            return "crypto_intel 'verify' requires both 'target' and 'expected' hash"
        res = verify_hash(target, expected=expected, algo=algo)
        return json.dumps(res, indent=2)

    elif op_clean in ("hmac", "sign"):
        if not target or not key:
            return "crypto_intel 'hmac' requires 'target' (data) and 'key'"
        res = compute_hmac(target, key=key, algo=algo)
        return json.dumps(res, indent=2)

    elif op_clean in ("token", "keygen", "secret", "uuid", "password"):
        res = generate_secret(token_type=target or op_clean, length=int(length or 32))
        return json.dumps(res, indent=2)

    elif op_clean in ("pbkdf2", "kdf", "derive"):
        if not target:
            return "crypto_intel 'pbkdf2' requires 'target' (password string)"
        res = derive_pbkdf2(target, salt=salt, iterations=int(iterations or 100000), algo=algo)
        return json.dumps(res, indent=2)

    return f"unknown crypto_intel op '{op}' - supported: hash, verify, hmac, token, pbkdf2"
