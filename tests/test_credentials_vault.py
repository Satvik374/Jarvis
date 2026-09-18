"""Unit tests for Windows Credential Manager & DPAPI Security Vault."""

from __future__ import annotations

import base64
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from jarvis.config import Config
from jarvis.perception.elements import Observation
from jarvis.security import (
    CredentialVault,
    delete_secret,
    dpapi_decrypt,
    dpapi_encrypt,
    get_credential_vault,
    get_secret,
    list_secrets,
    set_secret,
)
from jarvis.tools.registry import execute


class TestCredentialsVault(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.vault_file = Path(self.temp_dir.name) / "test_vault.enc"
        self.vault = CredentialVault(vault_file=self.vault_file)
        self.test_key = "JARVIS_TEST_API_KEY"
        self.test_val = "sk-test-1234567890abcdef"
        self.credentials = {}
        credman = SimpleNamespace(
            CRED_TYPE_GENERIC=1,
            CRED_PERSIST_LOCAL_MACHINE=2,
            CredWrite=lambda cred, flags: self.credentials.update({cred["TargetName"]: dict(cred)}),
            CredRead=lambda target, *args: self.credentials[target],
            CredDelete=lambda target, *args: self.credentials.pop(target),
            CredEnumerate=lambda *args: list(self.credentials.values()),
        )
        self.crypto = SimpleNamespace(
            CryptProtectData=Mock(side_effect=lambda data, *args: b"test-encrypted:" + data),
            CryptUnprotectData=Mock(side_effect=lambda data, *args: ("", data.removeprefix(b"test-encrypted:"))),
        )
        for mocked in (
            patch.dict(sys.modules, {"win32cred": credman, "win32crypt": self.crypto}),
            patch.dict(os.environ, {}, clear=True),
            patch("jarvis.security.vault._GLOBAL_VAULT", self.vault),
        ):
            mocked.start()
            self.addCleanup(mocked.stop)

    def test_credman_crud(self):
        """Test Windows Credential Manager write, read, list, and delete."""
        # 1. Write
        ok = self.vault.write_credman(self.test_key, self.test_val)
        self.assertTrue(ok)

        # 2. Read
        read_val = self.vault.read_credman(self.test_key)
        self.assertEqual(read_val, self.test_val)

        # 3. List
        all_creds = self.vault.list_credman()
        keys = [c["key"] for c in all_creds]
        self.assertIn(self.test_key, keys)

        # 4. Delete
        deleted = self.vault.delete_credman(self.test_key)
        self.assertTrue(deleted)
        self.assertIsNone(self.vault.read_credman(self.test_key))

    def test_dpapi_crypto(self):
        """Test Windows DPAPI encryption and decryption."""
        sample_text = "Highly-Sensitive-Master-Token-2026!@#"
        encrypted = self.vault.dpapi_encrypt(sample_text)
        self.assertIsInstance(encrypted, bytes)
        self.assertNotEqual(encrypted, sample_text.encode("utf-8"))

        decrypted = self.vault.dpapi_decrypt(encrypted)
        self.assertEqual(decrypted, sample_text)

    def test_dpapi_vault_crud(self):
        """Test DPAPI encrypted local vault CRUD."""
        # 1. Write
        ok = self.vault.write_dpapi("DPAPI_SECRET_KEY", "my_secret_value_999")
        self.assertTrue(ok)
        self.assertTrue(self.vault_file.exists())

        # 2. Read
        # Create fresh instance pointing to same file
        fresh_vault = CredentialVault(vault_file=self.vault_file)
        val = fresh_vault.read_dpapi("DPAPI_SECRET_KEY")
        self.assertEqual(val, "my_secret_value_999")

        # 3. List
        listed = fresh_vault.list_dpapi()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["key"], "DPAPI_SECRET_KEY")

        # 4. Delete
        del_ok = fresh_vault.delete_dpapi("DPAPI_SECRET_KEY")
        self.assertTrue(del_ok)
        self.assertIsNone(fresh_vault.read_dpapi("DPAPI_SECRET_KEY"))

    def test_unified_get_secret_fallback(self):
        """Test fallback priority: CredMan -> DPAPI -> Environment."""
        k = "FALLBACK_TEST_KEY"
        # 1. In environment
        os.environ[k] = "env_val"
        self.assertEqual(self.vault.get_secret(k), "env_val")

        # 2. In DPAPI (shadows env)
        self.vault.write_dpapi(k, "dpapi_val")
        self.assertEqual(self.vault.get_secret(k), "dpapi_val")

        # 3. In CredMan (shadows DPAPI)
        self.vault.write_credman(k, "credman_val")
        self.assertEqual(self.vault.get_secret(k), "credman_val")

        # Cleanup
        self.vault.delete_credman(k)
        self.vault.delete_dpapi(k)
        os.environ.pop(k, None)

    def test_encryption_failure_does_not_store_encoded_plaintext(self):
        self.crypto.CryptProtectData.side_effect = RuntimeError("encryption unavailable")
        self.assertFalse(self.vault.write_dpapi("TOKEN", "private-value"))
        self.assertFalse(self.vault_file.exists())
        self.assertIsNone(self.vault.read_dpapi("TOKEN"))
        self.crypto.CryptProtectData.side_effect = lambda data, *args: b"test-encrypted:" + data
        self.assertTrue(self.vault.write_dpapi("TOKEN", "original"))
        before = self.vault_file.read_bytes()
        self.crypto.CryptProtectData.side_effect = RuntimeError("encryption unavailable")
        self.assertFalse(self.vault.write_dpapi("TOKEN", "replacement"))
        self.assertEqual(self.vault_file.read_bytes(), before)
        self.assertEqual(self.vault.read_dpapi("TOKEN"), "original")

    def test_corrupt_vault_is_not_overwritten(self):
        for payload in (b"invalid data", b"test-encrypted:[]", b"test-encrypted:",
                        b'test-encrypted:{"TOKEN": 123}'):
            with self.subTest(payload=payload):
                self.vault_file.write_bytes(payload)
                vault = CredentialVault(self.vault_file)
                self.assertIsNone(vault.read_dpapi("TOKEN"))
                self.assertFalse(vault.write_dpapi("TOKEN", "new-value"))
                self.assertEqual(self.vault_file.read_bytes(), payload)

    def test_legacy_encoded_vault_is_readable_and_reencrypted(self):
        self.vault_file.write_bytes(base64.b64encode(b'{"TOKEN": "legacy"}'))
        self.crypto.CryptUnprotectData.side_effect = RuntimeError("legacy encoding")
        self.assertEqual(self.vault.read_dpapi("TOKEN"), "legacy")
        self.assertTrue(self.vault.write_dpapi("TOKEN", "updated"))
        self.assertTrue(self.vault_file.read_bytes().startswith(b"test-encrypted:"))

    def test_failed_save_preserves_file_and_cached_values(self):
        self.assertTrue(self.vault.write_dpapi("TOKEN", "original"))
        before = self.vault_file.read_bytes()
        with patch("os.replace", side_effect=OSError("replacement failed")):
            self.assertFalse(self.vault.write_dpapi("TOKEN", "replacement"))
            self.assertEqual(self.vault.read_dpapi("TOKEN"), "original")
            self.assertFalse(self.vault.delete_dpapi("TOKEN"))
        self.assertEqual(self.vault.read_dpapi("TOKEN"), "original")
        self.assertEqual(self.vault_file.read_bytes(), before)
        self.assertEqual(list(self.vault_file.parent.iterdir()), [self.vault_file])

    def test_mask_secret(self):
        """Test secret masking for safe logs/display."""
        self.assertEqual(self.vault.mask_secret(""), "")
        self.assertEqual(self.vault.mask_secret("short"), "••••••••")
        masked = self.vault.mask_secret("sk-openai-1234567890abcdef")
        self.assertTrue(masked.startswith("sk-o"))
        self.assertTrue(masked.endswith("cdef"))
        self.assertIn("••••", masked)

    def test_migrate_from_env(self):
        """Test migration of .env file secrets into Windows Credential Manager."""
        env_file = Path(self.temp_dir.name) / ".env"
        env_file.write_text("OPENAI_API_KEY=sk-test-migrate-112233\nWEATHER_API_KEY=weather-token-7788\n", encoding="utf-8")

        migrated = self.vault.migrate_from_env(env_path=env_file)
        self.assertIn("OPENAI_API_KEY", migrated)
        self.assertIn("WEATHER_API_KEY", migrated)

        # Verify values stored
        self.assertEqual(self.vault.read_credman("OPENAI_API_KEY"), "sk-test-migrate-112233")
        self.assertEqual(self.vault.read_credman("WEATHER_API_KEY"), "weather-token-7788")

        # Cleanup
        self.vault.delete_credman("OPENAI_API_KEY")
        self.vault.delete_credman("WEATHER_API_KEY")

    def test_tool_action_secret(self):
        """Test agent action 'secret' execution via registry."""
        cfg = Config()
        obs = Observation(elements=[], screen_size=(1920, 1080), active_window="Test")

        # 1. Set secret
        res = execute("secret", {"op": "set", "key": "TOOL_TEST_KEY", "value": "super_secret_val_123"}, obs, cfg)
        self.assertTrue(res.ok)
        self.assertIn("TOOL_TEST_KEY", res.message)

        # 2. Get secret
        res = execute("secret", {"op": "get", "key": "TOOL_TEST_KEY"}, obs, cfg)
        self.assertTrue(res.ok)
        self.assertIn("TOOL_TEST_KEY", res.message)

        # 3. List secrets
        res = execute("secret", {"op": "list"}, obs, cfg)
        self.assertTrue(res.ok)
        self.assertIn("TOOL_TEST_KEY", res.message)

        # 4. Delete secret
        res = execute("secret", {"op": "delete", "key": "TOOL_TEST_KEY"}, obs, cfg)
        self.assertTrue(res.ok)
        self.assertIn("Deleted", res.message)


if __name__ == "__main__":
    unittest.main()
