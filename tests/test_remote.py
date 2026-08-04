"""Focused security and protocol checks for Jarvis Remote."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from jarvis import remote


class RemoteCryptoTests(unittest.TestCase):
    def test_devices_derive_same_secret_and_authenticate_payload(self):
        controller = remote.create_identity()
        agent = remote.create_identity()
        controller_secret = remote.derive_secret(
            controller.kx_private, agent.kx_public, "pair-123")
        agent_secret = remote.derive_secret(agent.kx_private, controller.kx_public, "pair-123")
        self.assertEqual(controller_secret, agent_secret)

        envelope = remote.encrypt_payload(
            controller_secret, controller.sign_private, "pair-123", "controller", "agent",
            {"type": "task", "id": "task-1", "task": "open calculator"},
        )
        decoded = remote.decrypt_payload(
            agent_secret, controller.sign_public, "pair-123", "controller", "agent", envelope)
        self.assertEqual(decoded["task"], "open calculator")

    def test_tampered_payload_is_rejected(self):
        controller = remote.create_identity()
        agent = remote.create_identity()
        secret = remote.derive_secret(controller.kx_private, agent.kx_public, "pair-456")
        envelope = remote.encrypt_payload(secret, controller.sign_private, "pair-456",
                                          "controller", "agent", {"type": "task"})
        envelope["ciphertext"] = envelope["ciphertext"][:-2] + "AA"
        with self.assertRaises(remote.RemoteSecurityError):
            remote.decrypt_payload(secret, controller.sign_public, "pair-456",
                                   "controller", "agent", envelope)

    def test_untrusted_pairing_cannot_be_used(self):
        pair = remote.Pairing(
            label="Office PC", endpoint="https://relay.example", pair_id="pair-789",
            role="controller", peer_name="Office PC", local_name="Laptop",
            secret=remote._b64(b"x" * 32), sign_private="unused", peer_sign_public="unused",
        )
        with self.assertRaises(remote.RemoteSecurityError):
            remote._require_trusted(pair)

    def test_replayed_task_id_is_ignored_after_first_processing(self):
        pair = remote.Pairing(
            label="Office PC", endpoint="https://relay.example", pair_id="pair-replay",
            role="agent", peer_name="Laptop", local_name="Office PC",
            secret=remote._b64(b"x" * 32), sign_private="unused", peer_sign_public="unused",
            trusted=True,
        )
        message = {"type": "task", "id": "same-task", "task": "open calculator"}
        self.assertFalse(remote._already_processed(pair, message))
        self.assertTrue(remote._already_processed(pair, message))


class LocalPairingStateTests(unittest.TestCase):
    def test_store_round_trips_private_pairing_material(self):
        with tempfile.TemporaryDirectory() as temp:
            store = remote.PairingStore(temp)
            pair = remote.Pairing(
                label="Office PC", endpoint="https://relay.example", pair_id="pair-state",
                role="controller", peer_name="Office PC", local_name="Laptop",
                secret=remote._b64(b"s" * 32), sign_private="private", peer_sign_public="public",
                trusted=True, received_sequence=4,
            )
            store.save(pair)
            loaded = store.get("office pc", role="controller")
            self.assertTrue(loaded.trusted)
            self.assertEqual(loaded.received_sequence, 4)
            self.assertEqual(loaded.secret_bytes, b"s" * 32)
            self.assertTrue((Path(temp) / "pairings.json").exists())


try:
    import fastapi  # noqa: F401 - relay-only dependency
    from relay_server.main import app as _relay_app
except ImportError:
    _RELAY_DEPS_AVAILABLE = False
else:
    _RELAY_DEPS_AVAILABLE = True


@unittest.skipUnless(_RELAY_DEPS_AVAILABLE, "relay_server dependencies are not installed")
class RelayProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The relay reads this only at import time. Set a per-test temporary
        # path before loading it so testing never creates project state.
        cls._temp = tempfile.TemporaryDirectory()
        os.environ["RELAY_STATE_PATH"] = str(Path(cls._temp.name) / "relay.json")
        from fastapi.testclient import TestClient
        cls.client = TestClient(_relay_app)

    @classmethod
    def tearDownClass(cls):
        cls._temp.cleanup()

    def test_pair_then_relay_authenticated_envelope(self):
        controller = remote.create_identity()
        agent = remote.create_identity()
        made = self.client.post("/v1/pairings", json={
            "name": "Laptop", "kx_public": controller.kx_public,
            "sign_public": controller.sign_public,
        })
        self.assertEqual(made.status_code, 200)
        created = made.json()
        claimed = self.client.post("/v1/pairings/claim", json={
            "code": created["code"], "name": "Office PC", "kx_public": agent.kx_public,
            "sign_public": agent.sign_public,
        })
        self.assertEqual(claimed.status_code, 200)
        pair_id = created["pair_id"]
        secret = remote.derive_secret(controller.kx_private, agent.kx_public, pair_id)
        envelope = remote.encrypt_payload(secret, controller.sign_private, pair_id,
                                          "controller", "agent", {"type": "task", "id": "a"})
        sent = self.client.post(f"/v1/pairs/{pair_id}/messages", json={
            "sender": "controller", "envelope": envelope,
        })
        self.assertEqual(sent.status_code, 200)
        polled = self.client.get(f"/v1/pairs/{pair_id}/messages",
                                 params={"recipient": "agent", "after": 0, "timeout": 0})
        self.assertEqual(polled.status_code, 200)
        message = polled.json()["messages"][0]
        self.assertEqual(remote.decrypt_payload(secret, controller.sign_public, pair_id,
                                                 "controller", "agent", message)["id"], "a")

        forged = dict(envelope)
        forged["signature"] = remote._b64(b"z" * 64)
        rejected = self.client.post(f"/v1/pairs/{pair_id}/messages", json={
            "sender": "controller", "envelope": forged,
        })
        self.assertEqual(rejected.status_code, 401)


if __name__ == "__main__":
    unittest.main()
