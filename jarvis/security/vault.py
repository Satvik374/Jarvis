"""Windows Credential Manager and DPAPI Security Vault for Jarvis.

Provides enterprise-grade protection for API keys, tokens, and credentials:
1. Windows Credential Manager (win32cred): Stores credentials directly in the
   Windows Vault under the 'JARVIS:<key>' target namespace.
2. Windows DPAPI (win32crypt): Hardware/user-master-key backed encryption
   for on-disk secrets and configuration payloads.
"""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from ..utils import logging as log

# Target prefix in Windows Credential Manager
CREDMAN_PREFIX = "JARVIS:"

# Known API keys and service tokens to scan for migration
KNOWN_SECRET_KEYS = [
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "HF_TOKEN",
    "HUGGINGFACE_TOKEN",
    "GITHUB_TOKEN",
    "GMAIL_ADDRESS",
    "GMAIL_APP_PASSWORD",
    "DISCORD_BOT_TOKEN",
    "WHATSAPP_TOKEN",
    "WHATSAPP_PHONE_ID",
    "SPOTIFY_CLIENT_ID",
    "SPOTIFY_CLIENT_SECRET",
    "WEATHER_API_KEY",
    "LINEAR_API_KEY",
    "SLACK_BOT_TOKEN",
    "NOTION_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "GROQ_API_KEY",
    "PERPLEXITY_API_KEY",
    "DEEPSEEK_API_KEY",
    "MISTRAL_API_KEY",
]


class CredentialVault:
    """Secure Credential Vault backed by Windows Credential Manager and DPAPI."""

    def __init__(self, vault_file: Optional[Path] = None):
        if vault_file is None:
            proj_root = Path(__file__).resolve().parent.parent.parent
            self.vault_file = proj_root / "dataset" / "data" / "vault.enc"
        else:
            self.vault_file = Path(vault_file)
        self.vault_file.parent.mkdir(parents=True, exist_ok=True)
        self._dpapi_cache: Optional[Dict[str, str]] = None

    # ------------------------------------------------------------------ #
    # 1. Windows Credential Manager (win32cred)
    # ------------------------------------------------------------------ #

    def _credman_target(self, key: str) -> str:
        k = key.strip().upper()
        if not k.startswith(CREDMAN_PREFIX):
            k = f"{CREDMAN_PREFIX}{k}"
        return k

    def write_credman(self, key: str, secret: str, username: str = "JarvisUser", description: str = "Jarvis Managed Secret") -> bool:
        """Write a generic credential to Windows Credential Manager."""
        target = self._credman_target(key)
        try:
            import win32cred

            cred_dict = {
                "Type": win32cred.CRED_TYPE_GENERIC,
                "TargetName": target,
                "UserName": username,
                "CredentialBlob": secret,
                "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
                "Comment": description,
            }
            try:
                win32cred.CredWrite(cred_dict, 0)
                return True
            except Exception:
                cred_dict["CredentialBlob"] = secret.encode("utf-16le")
                win32cred.CredWrite(cred_dict, 0)
                return True
        except Exception as exc:
            log.warn(f"CredMan write failed for '{key}': {exc}")
            return False

    def read_credman(self, key: str) -> Optional[str]:
        """Read a generic credential from Windows Credential Manager."""
        target = self._credman_target(key)
        try:
            import win32cred

            cred_dict = win32cred.CredRead(target, win32cred.CRED_TYPE_GENERIC, 0)
            blob = cred_dict.get("CredentialBlob")
            if blob is None:
                return None
            if isinstance(blob, str):
                return blob
            if isinstance(blob, bytes):
                try:
                    return blob.decode("utf-16le")
                except UnicodeDecodeError:
                    return blob.decode("utf-8", errors="ignore")
            return str(blob)
        except Exception:
            return None

    def delete_credman(self, key: str) -> bool:
        """Delete a generic credential from Windows Credential Manager."""
        target = self._credman_target(key)
        try:
            import win32cred

            win32cred.CredDelete(target, win32cred.CRED_TYPE_GENERIC, 0)
            return True
        except Exception:
            return False

    def list_credman(self) -> List[Dict[str, str]]:
        """List all credentials managed by Jarvis in Windows Credential Manager."""
        results: List[Dict[str, str]] = []
        try:
            import win32cred

            creds = win32cred.CredEnumerate(None, 0)
            for c in creds:
                t = c.get("TargetName", "")
                if t.startswith(CREDMAN_PREFIX):
                    clean_key = t[len(CREDMAN_PREFIX):]
                    blob = c.get("CredentialBlob")
                    secret_str = ""
                    if blob:
                        if isinstance(blob, str):
                            secret_str = blob
                        elif isinstance(blob, bytes):
                            try:
                                secret_str = blob.decode("utf-16le")
                            except Exception:
                                secret_str = blob.decode("utf-8", errors="ignore")
                        else:
                            secret_str = str(blob)
                    results.append({
                        "key": clean_key,
                        "target": t,
                        "username": c.get("UserName", ""),
                        "comment": c.get("Comment", ""),
                        "backend": "credman",
                        "masked": self.mask_secret(secret_str),
                    })
        except Exception:
            pass
        return results

    # ------------------------------------------------------------------ #
    # 2. Windows DPAPI Cryptographic Store (win32crypt)
    # ------------------------------------------------------------------ #

    def dpapi_encrypt(self, data: Union[str, bytes], entropy: Optional[bytes] = None) -> bytes:
        """Encrypt data using Windows DPAPI (CryptProtectData)."""
        raw_bytes = data.encode("utf-8") if isinstance(data, str) else data
        try:
            import win32crypt
            # CryptProtectData(DataIn, Description, OptionalEntropy, Reserved, PromptStruct, Flags)
            return win32crypt.CryptProtectData(raw_bytes, "Jarvis DPAPI Secret", entropy, None, None, 0)
        except Exception as exc:
            log.warn(f"DPAPI encryption failed ({exc}); falling back to base64 encoding.")
            return base64.b64encode(raw_bytes)

    def dpapi_decrypt(self, blob: bytes, entropy: Optional[bytes] = None) -> str:
        """Decrypt data using Windows DPAPI (CryptUnprotectData)."""
        try:
            import win32crypt
            _, decrypted = win32crypt.CryptUnprotectData(blob, entropy, None, None, 0)
            return decrypted.decode("utf-8")
        except Exception:
            try:
                return base64.b64decode(blob).decode("utf-8")
            except Exception as exc:
                log.warn(f"DPAPI decryption failed: {exc}")
                return ""

    def _load_dpapi_vault(self) -> Dict[str, str]:
        if self._dpapi_cache is not None:
            return self._dpapi_cache
        if not self.vault_file.exists():
            self._dpapi_cache = {}
            return self._dpapi_cache

        try:
            with open(self.vault_file, "rb") as f:
                encrypted_blob = f.read()
            decrypted_json = self.dpapi_decrypt(encrypted_blob)
            self._dpapi_cache = json.loads(decrypted_json) if decrypted_json else {}
        except Exception as exc:
            log.warn(f"Error loading DPAPI vault from {self.vault_file}: {exc}")
            self._dpapi_cache = {}
        return self._dpapi_cache

    def _save_dpapi_vault(self) -> bool:
        if self._dpapi_cache is None:
            return True
        try:
            raw_json = json.dumps(self._dpapi_cache, indent=2)
            encrypted = self.dpapi_encrypt(raw_json)
            with open(self.vault_file, "wb") as f:
                f.write(encrypted)
            return True
        except Exception as exc:
            log.error(f"Error writing DPAPI vault: {exc}")
            return False

    def write_dpapi(self, key: str, secret: str) -> bool:
        """Store a secret in the DPAPI-encrypted vault file."""
        vault = self._load_dpapi_vault()
        vault[key.strip().upper()] = secret
        return self._save_dpapi_vault()

    def read_dpapi(self, key: str) -> Optional[str]:
        """Read a secret from the DPAPI-encrypted vault file."""
        vault = self._load_dpapi_vault()
        return vault.get(key.strip().upper())

    def delete_dpapi(self, key: str) -> bool:
        """Delete a secret from the DPAPI-encrypted vault file."""
        vault = self._load_dpapi_vault()
        k = key.strip().upper()
        if k in vault:
            del vault[k]
            return self._save_dpapi_vault()
        return False

    def list_dpapi(self) -> List[Dict[str, str]]:
        """List all keys stored in the DPAPI vault."""
        vault = self._load_dpapi_vault()
        results = []
        for k, v in vault.items():
            results.append({
                "key": k,
                "backend": "dpapi",
                "masked": self.mask_secret(v),
            })
        return results

    # ------------------------------------------------------------------ #
    # 3. Unified Secret Access & Management API
    # ------------------------------------------------------------------ #

    def get_secret(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Unified lookup: CredMan -> DPAPI Vault -> os.environ -> default."""
        k = key.strip().upper()

        # 1. Windows Credential Manager
        val = self.read_credman(k)
        if val:
            return val

        # 2. DPAPI Vault
        val = self.read_dpapi(k)
        if val:
            return val

        # 3. Environment Variable
        env_val = os.getenv(k) or os.getenv(key)
        if env_val:
            return env_val

        return default

    def set_secret(self, key: str, secret: str, backend: str = "credman") -> bool:
        """Store a secret into Windows Credential Manager and/or DPAPI Vault."""
        k = key.strip().upper()
        s = secret.strip()
        if not k or not s:
            return False

        if backend == "dpapi":
            return self.write_dpapi(k, s)
        elif backend == "all":
            ok1 = self.write_credman(k, s)
            ok2 = self.write_dpapi(k, s)
            return ok1 or ok2
        else:  # default: credman
            ok = self.write_credman(k, s)
            if not ok:
                # Fallback to DPAPI
                return self.write_dpapi(k, s)
            return True

    def delete_secret(self, key: str) -> bool:
        """Remove a secret from both Credential Manager and DPAPI."""
        k = key.strip().upper()
        ok1 = self.delete_credman(k)
        ok2 = self.delete_dpapi(k)
        return ok1 or ok2

    def list_secrets(self) -> List[Dict[str, str]]:
        """List all secrets found across CredMan, DPAPI, and configured environment."""
        seen = {}

        # 1. CredMan
        for c in self.list_credman():
            seen[c["key"]] = c

        # 2. DPAPI
        for d in self.list_dpapi():
            if d["key"] not in seen:
                seen[d["key"]] = d
            else:
                seen[d["key"]]["backend"] += "+dpapi"

        # 3. Known Env variables
        for k in KNOWN_SECRET_KEYS:
            env_val = os.getenv(k)
            if env_val and k not in seen:
                seen[k] = {
                    "key": k,
                    "backend": "env",
                    "masked": self.mask_secret(env_val),
                }

        results = list(seen.values())
        results.sort(key=lambda x: x["key"])
        return results

    def mask_secret(self, secret: str) -> str:
        """Mask a sensitive string for display (e.g. sk-12345678 -> sk-...5678)."""
        if not secret:
            return ""
        if len(secret) <= 8:
            return "••••••••"
        prefix = secret[:4]
        suffix = secret[-4:]
        return f"{prefix}••••{suffix}"

    def migrate_from_env(self, env_path: Optional[Path] = None) -> List[str]:
        """Scan a .env file or active environment for secrets and migrate into CredMan."""
        migrated: List[str] = []

        if env_path is None:
            proj_root = Path(__file__).resolve().parent.parent.parent
            env_path = proj_root / ".env"

        env_vars: Dict[str, str] = {}
        if env_path.exists():
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'\"")
                        if k and v:
                            env_vars[k] = v
            except Exception as exc:
                log.warn(f"Error reading {env_path}: {exc}")

        # Also check current environment
        for k in KNOWN_SECRET_KEYS:
            if k not in env_vars and os.getenv(k):
                env_vars[k] = os.getenv(k)

        for k, v in env_vars.items():
            if any(k.upper().startswith(prefix) for prefix in ["OPENAI", "GEMINI", "ANTHROPIC", "HF", "GITHUB", "GMAIL", "DISCORD", "WHATSAPP", "SPOTIFY", "WEATHER", "SLACK", "LINEAR", "NOTION"]):
                if self.set_secret(k, v, backend="credman"):
                    migrated.append(k)

        return migrated


_GLOBAL_VAULT: Optional[CredentialVault] = None


def get_credential_vault(vault_file: Optional[Path] = None) -> CredentialVault:
    global _GLOBAL_VAULT
    if _GLOBAL_VAULT is None or vault_file is not None:
        _GLOBAL_VAULT = CredentialVault(vault_file=vault_file)
    return _GLOBAL_VAULT


def get_secret(key: str, default: Optional[str] = None) -> Optional[str]:
    """Convenience global accessor for secrets."""
    return get_credential_vault().get_secret(key, default=default)


def set_secret(key: str, value: str, backend: str = "credman") -> bool:
    """Convenience global setter for secrets."""
    return get_credential_vault().set_secret(key, value, backend=backend)


def delete_secret(key: str) -> bool:
    """Convenience global deleter for secrets."""
    return get_credential_vault().delete_secret(key)


def list_secrets() -> List[Dict[str, str]]:
    """Convenience global list for secrets."""
    return get_credential_vault().list_secrets()


def dpapi_encrypt(data: Union[str, bytes], entropy: Optional[bytes] = None) -> bytes:
    """Encrypt using Windows DPAPI."""
    return get_credential_vault().dpapi_encrypt(data, entropy=entropy)


def dpapi_decrypt(blob: bytes, entropy: Optional[bytes] = None) -> str:
    """Decrypt using Windows DPAPI."""
    return get_credential_vault().dpapi_decrypt(blob, entropy=entropy)
