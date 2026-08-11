"""Focused security and protocol checks for Jarvis Remote."""

from __future__ import annotations

import os
import base64
import tempfile
import unittest
from pathlib import Path

from jarvis import remote
from jarvis.agent.loop import Agent
from jarvis.config import Config
from jarvis.tools import registry


class _ChatBrain:
    """Captures the lightweight chat gate's prompt without using a model."""

    def __init__(self) -> None:
        self.system = ""

    def complete(self, system, messages, image=None):
        self.system = system
        return '{"mode":"chat","reply":"Yes, the paired device is available."}'


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
                trusted=True, received_sequence=4, peer_kind="android",
                peer_capabilities=["open <app or URL>", "screenshot"],
                peer_status={"accessibility_ready": True},
            )
            store.save(pair)
            loaded = store.get("office pc", role="controller")
            self.assertTrue(loaded.trusted)
            self.assertEqual(loaded.received_sequence, 4)
            self.assertEqual(loaded.secret_bytes, b"s" * 32)
            self.assertEqual(loaded.peer_kind, "android")
            self.assertIn("screenshot", loaded.peer_capabilities)
            self.assertTrue(loaded.peer_status["accessibility_ready"])
            self.assertTrue((Path(temp) / "pairings.json").exists())

    def test_authenticated_mobile_screenshot_is_saved_and_returned(self):
        # Valid 1x1 PNG; the transport intentionally caps remote image bytes.
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
            "+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        with tempfile.TemporaryDirectory() as temp:
            store = remote.PairingStore(temp)
            pair = remote.Pairing(
                label="Mobile", endpoint="https://relay.example", pair_id="pair-mobile",
                role="controller", peer_name="Pixel", local_name="Laptop",
                secret=remote._b64(b"s" * 32), sign_private="private",
                peer_sign_public="public", trusted=True,
            )
            ok, message, image_path = remote._task_response(store, pair, "task-shot", {
                "type": "task_result", "task_id": "task-shot", "ok": True,
                "result": "Captured.", "device_kind": "android",
                "capabilities": ["screenshot", "open <app or URL>"],
                "device_status": {"accessibility_ready": True, "screenshot_ready": True},
                "attachment": {"mime_type": "image/png", "width": 1080, "height": 2400,
                               "data": remote._b64(png)},
            })

            self.assertTrue(ok)
            self.assertIsNotNone(image_path)
            self.assertTrue(Path(image_path).is_file())
            self.assertEqual(Path(image_path).read_bytes(), png)
            self.assertIn("mobile screenshot (1080x2400) saved", message)
            self.assertEqual(pair.peer_kind, "android")
            self.assertTrue(pair.peer_status["screenshot_ready"])

    def test_oversized_remote_screenshot_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            store = remote.PairingStore(temp)
            pair = remote.Pairing(
                label="Mobile", endpoint="https://relay.example", pair_id="pair-mobile",
                role="controller", peer_name="Pixel", local_name="Laptop",
                secret=remote._b64(b"s" * 32), sign_private="private",
                peer_sign_public="public", trusted=True,
            )
            ok, message, image_path = remote._task_response(store, pair, "task-shot", {
                "ok": True, "result": "Captured.",
                "attachment": {"mime_type": "image/jpeg",
                               "data": remote._b64(b"\xff\xd8\xff" + b"x" * remote.MAX_REMOTE_IMAGE_BYTES)},
            })

            self.assertFalse(ok)
            self.assertIsNone(image_path)
            self.assertIn("exceeds", message)

    def test_store_removes_pairing_by_name_or_id(self):
        with tempfile.TemporaryDirectory() as temp:
            store = remote.PairingStore(temp)
            for label, pair_id in (("Mobile", "pair-mobile"), ("Office PC", "pair-office")):
                store.save(remote.Pairing(
                    label=label, endpoint="https://relay.example", pair_id=pair_id,
                    role="controller", peer_name=label, local_name="Laptop",
                    secret=remote._b64(b"s" * 32), sign_private="private",
                    peer_sign_public="public", trusted=True,
                ))

            removed = store.remove("mobile")
            self.assertEqual([pair.pair_id for pair in removed], ["pair-mobile"])
            self.assertEqual([pair.label for pair in store.list()], ["Office PC"])
            self.assertEqual(store.remove("pair-office")[0].label, "Office PC")
            self.assertEqual(store.list(), [])

    def test_device_list_includes_pairing_id_name_role_and_state(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg = Config()
            cfg.remote.state_dir = temp
            remote.PairingStore(temp).save(remote.Pairing(
                label="Mobile", endpoint="https://relay.example", pair_id="pair-mobile-123",
                role="controller", peer_name="Pixel 8", local_name="Laptop",
                secret=remote._b64(b"s" * 32), sign_private="private",
                peer_sign_public="public", trusted=True,
            ))

            output = remote.devices_text(cfg)
            self.assertIn("id: pair-mobile-123", output)
            self.assertIn("name: Mobile", output)
            self.assertIn("local: Laptop | peer: Pixel 8", output)
            self.assertIn("role: controller | state: trusted", output)

    def test_console_remove_deletes_local_pairing(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg = Config()
            cfg.remote.state_dir = temp
            remote.PairingStore(temp).save(remote.Pairing(
                label="My Phone", endpoint="https://relay.example", pair_id="pair-phone",
                role="controller", peer_name="Pixel", local_name="Laptop",
                secret=remote._b64(b"s" * 32), sign_private="private",
                peer_sign_public="public", trusted=True,
            ))

            output = remote.console_command(":remote remove My Phone", cfg)
            self.assertIn("Removed local pairing", output)
            self.assertEqual(remote.PairingStore(temp).list(), [])


class ChatRemoteContextTests(unittest.TestCase):
    def test_chat_gate_receives_trusted_device_context(self):
        """Status questions must not get a stale local-only answer."""
        with tempfile.TemporaryDirectory() as temp:
            cfg = Config()
            cfg.data.collect_trajectories = False
            cfg.data.trajectory_dir = temp
            cfg.remote.state_dir = temp
            remote.PairingStore(temp).save(remote.Pairing(
                label="Office PC", endpoint="https://relay.example", pair_id="pair-chat",
                role="controller", peer_name="Office PC", local_name="Laptop",
                secret=remote._b64(b"s" * 32), sign_private="private", peer_sign_public="public",
                trusted=True,
            ))
            brain = _ChatBrain()
            reply = Agent(brain, cfg)._maybe_chat("Can you control my second computer?")

        self.assertEqual(reply, "Yes, the paired device is available.")
        self.assertIn("Trusted remote devices: Office PC", brain.system)

    def test_android_context_lists_only_reported_mobile_commands(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg = Config()
            cfg.remote.state_dir = temp
            remote.PairingStore(temp).save(remote.Pairing(
                label="Mobile", endpoint="https://relay.example", pair_id="pair-chat-mobile",
                role="controller", peer_name="Pixel", local_name="Laptop",
                secret=remote._b64(b"s" * 32), sign_private="private",
                peer_sign_public="public", trusted=True, peer_kind="android",
                peer_capabilities=["open <app or URL>", "screenshot", "capabilities"],
                peer_status={"accessibility_ready": True, "screenshot_ready": True},
            ))

            context = remote.note(cfg)

        self.assertIn("Mobile: Android", context)
        self.assertIn("Supported commands: open <app or URL>, screenshot, capabilities", context)
        self.assertIn("never invent a remote tool", context.lower())
        self.assertIn("ALWAYS act with that element ID", context)
        self.assertIn("never estimate coordinates", context)
        self.assertIn("IDs expire after any screen-changing action", context)


class RemoteConfirmationTests(unittest.TestCase):
    def test_default_remote_policy_runs_automatically(self):
        cfg = Config()
        cfg.safety.confirm_each_action = True

        unattended = remote._configure_remote_confirmation(cfg, allow_unattended=False)

        self.assertTrue(unattended)
        self.assertFalse(cfg.safety.confirm_each_action)

    def test_confirmation_can_be_enabled_per_target_device(self):
        cfg = Config()
        cfg.remote.require_confirmation = True

        unattended = remote._configure_remote_confirmation(cfg, allow_unattended=False)

        self.assertFalse(unattended)
        self.assertTrue(cfg.safety.confirm_each_action)

    def test_launch_override_keeps_legacy_unattended_option_working(self):
        cfg = Config()
        cfg.remote.require_confirmation = True

        unattended = remote._configure_remote_confirmation(cfg, allow_unattended=True)

        self.assertTrue(unattended)
        self.assertFalse(cfg.safety.confirm_each_action)


class RemoteActionImageTests(unittest.TestCase):
    def test_remote_action_without_new_screenshot_invalidates_old_image(self):
        from unittest.mock import patch
        cfg = Config()
        with patch("jarvis.remote.send_task",
                   return_value=(True, "Mobile: clicked element 7", None)):
            result = registry._h_remote_task(
                {"device": "Mobile", "task": "tap element 7"}, None, cfg)

        self.assertTrue(result.ok)
        self.assertTrue(result.clear_image)
        self.assertIsNone(result.image_path)

    def test_remote_screenshot_replaces_instead_of_clearing_image(self):
        from unittest.mock import patch
        cfg = Config()
        cfg.brain.use_vision = True
        with patch("jarvis.remote.send_task",
                   return_value=(True, "Mobile: captured", "C:/tmp/mobile.jpg")):
            result = registry._h_remote_task(
                {"device": "Mobile", "task": "screenshot"}, None, cfg)

        self.assertFalse(result.clear_image)
        self.assertEqual(result.image_path, "C:/tmp/mobile.jpg")

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
