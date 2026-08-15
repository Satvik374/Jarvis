"""Unit tests for Windows Credential Manager & DPAPI Security Vault."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

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
        self.vault_file = Path(self.temp_dir.name) / "test_vault.enc"
        self.vault = CredentialVault(vault_file=self.vault_file)
        self.test_key = "JARVIS_TEST_API_KEY"
        self.test_val = "sk-test-1234567890abcdef"

    def tearDown(self):
        # Clean up any test keys left behind
        try:
            self.vault.delete_credman(self.test_key)
            self.vault.delete_dpapi(self.test_key)
        except Exception:
            pass
        self.temp_dir.cleanup()

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
