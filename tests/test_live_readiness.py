"""Live-voice readiness: what each path needs before it can start.

Every one of these tests exists because a dead voice session used to report a
WebSocket close code and nothing else. The assertion that matters is not "a
string was produced" but "the string names the credential that is missing".
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jarvis.config import Config
from jarvis.live import readiness


EMPTY_ENV: dict[str, str] = {}

#: A path that cannot exist, so ADC discovery never reaches the real machine.
NO_ADC = [Path("definitely-not-here") / "application_default_credentials.json"]
NO_MODEL = Path("definitely-not-here") / "kokoro-v1.0.onnx"


def _paths(config=None, env=None, vault=None, **kwargs) -> dict[str, readiness.VoicePath]:
    """`describe` with every ambient source pinned, so results are local-only.

    Without this the real Windows Credential Vault and the real gcloud ADC on
    the test machine leak in, and "nothing is configured" quietly passes because
    this box happens to have credentials.
    """
    if env is None:
        env = EMPTY_ENV
    if vault is None:
        vault = lambda name: None  # noqa: E731 - a stub, not a definition
    kwargs.setdefault("adc_candidates", NO_ADC)
    kwargs.setdefault("local_tts_model", NO_MODEL)
    return {
        path.key: path
        for path in readiness.describe(config, env, vault=vault, **kwargs)
    }


class ReadinessTestCase(unittest.TestCase):
    def setUp(self):
        readiness.clear_failures()

    def tearDown(self):
        readiness.clear_failures()

    def _config(self, **live_overrides) -> Config:
        cfg = Config()
        for name, value in live_overrides.items():
            setattr(cfg.live_voice, name, value)
        return cfg

    # -- the all-blocked case -------------------------------------------------

    def test_nothing_configured_blocks_every_conversation_path(self):
        """The honest default is "no", not a green tick."""
        paths = _paths(config=self._config(fish_agent_id="", ws_url=""))

        for key in ("fish", "gemini_api_key", "gemini_vertex", "relay"):
            self.assertFalse(paths[key].ready, f"{key} should be blocked")
        self.assertEqual(readiness.summary(paths.values())["voice_ready"], False)

    def test_every_blocked_path_offers_a_remedy(self):
        """A blocked path that cannot say how to unblock it is just noise."""
        paths = _paths(config=self._config(fish_agent_id="", ws_url=""))

        for key in ("gemini_api_key", "gemini_vertex", "relay"):
            self.assertTrue(paths[key].remedy, f"{key} must name a remedy")

    # -- the free API-key path ------------------------------------------------

    def test_a_gemini_api_key_makes_the_free_path_ready(self):
        paths = _paths(
            config=self._config(fish_agent_id="", ws_url=""),
            env={"JARVIS_LIVE_API_KEY": "AIzaSyTESTKEY"},
        )

        self.assertTrue(paths["gemini_api_key"].ready)
        self.assertEqual(
            readiness.summary(paths.values())["voice_ready_paths"], ["gemini_api_key"]
        )

    def test_the_missing_key_remedy_points_at_a_free_key(self):
        paths = _paths(config=self._config(api_key=""))

        remedy = paths["gemini_api_key"].remedy
        self.assertIn("JARVIS_LIVE_API_KEY", remedy)
        self.assertIn("aistudio.google.com", remedy)

    def test_key_resolution_prefers_config_then_env_then_vault(self):
        cfg = self._config(api_key="from-config")
        self.assertEqual(
            readiness.gemini_api_key(cfg, {"JARVIS_LIVE_API_KEY": "from-env"}), "from-config"
        )

        cfg = self._config(api_key="")
        self.assertEqual(
            readiness.gemini_api_key(cfg, {"JARVIS_LIVE_API_KEY": "from-env"}), "from-env"
        )

        vault = lambda name: {"GEMINI_API_KEY": "from-vault"}.get(name)
        self.assertEqual(readiness.gemini_api_key(cfg, {}, vault), "from-vault")

    def test_a_broken_vault_does_not_break_the_report(self):
        """A vault read that raises must degrade, not propagate."""
        def exploding_vault(name):
            raise RuntimeError("vault unavailable")

        self.assertEqual(readiness.gemini_api_key(self._config(api_key=""), {}, exploding_vault), "")

    # -- the Vertex path ------------------------------------------------------

    def test_vertex_is_ready_from_application_default_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            adc = Path(tmp) / "application_default_credentials.json"
            adc.write_text("{}", encoding="utf-8")
            paths = _paths(config=self._config(), adc_candidates=[adc])

        self.assertTrue(paths["gemini_vertex"].ready)
        self.assertIn(str(adc), paths["gemini_vertex"].detail)

    def test_missing_vertex_credentials_name_the_exact_command(self):
        absent = [Path("definitely-not-here") / "adc.json"]
        paths = _paths(config=self._config(), adc_candidates=absent)

        self.assertFalse(paths["gemini_vertex"].ready)
        self.assertIn("gcloud auth application-default login", paths["gemini_vertex"].remedy)

    # -- the relay path -------------------------------------------------------

    def test_the_relay_path_needs_a_url_and_says_so(self):
        paths = _paths(config=self._config(ws_url=""))
        self.assertFalse(paths["relay"].ready)
        self.assertIn("ws_url", paths["relay"].remedy)

        paths = _paths(config=self._config(ws_url="ws://127.0.0.1:8000/v1/realtime"))
        self.assertTrue(paths["relay"].ready)

    # -- local speech ---------------------------------------------------------

    def test_local_speech_is_reported_but_is_not_a_conversation_path(self):
        """Kokoro keeps Jarvis audible; it cannot hold a live session."""
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "kokoro-v1.0.onnx"
            model.write_text("stub", encoding="utf-8")
            paths = _paths(
                config=self._config(fish_agent_id="", ws_url=""),
                local_tts_model=model,
            )

        self.assertTrue(paths["local"].ready)
        # Even though local speech works, nothing can hold a conversation.
        summary = readiness.summary(paths.values())
        self.assertFalse(summary["voice_ready"])
        self.assertNotIn("local", summary["voice_ready_paths"])

    def test_a_missing_local_model_is_reported(self):
        paths = _paths(config=self._config(), local_tts_model=Path("no-such-model.onnx"))
        self.assertFalse(paths["local"].ready)
        self.assertTrue(paths["local"].remedy)

    # -- remembering a billing failure ----------------------------------------

    def test_a_remembered_credit_failure_marks_fish_unavailable(self):
        """A 402 is a billing state; another attempt cannot clear it."""
        config = self._config(fish_agent_id="agent-123")
        self.assertTrue(_paths(config=config)["fish"].ready)

        readiness.remember_failure("fish", "Fish Audio API error (402): Out of API credit")
        paths = _paths(config=config)

        self.assertFalse(paths["fish"].ready)
        self.assertTrue(paths["fish"].account_gated)
        self.assertIn("402", paths["fish"].detail)
        # The remedy must not send the user back to the same dead end.
        self.assertIn("Gemini", paths["fish"].remedy)

    def test_clearing_the_credit_failure_restores_the_path(self):
        """A later success has to be able to un-stick the report."""
        config = self._config(fish_agent_id="agent-123")
        readiness.remember_failure("fish", "out of credit")
        self.assertFalse(_paths(config=config)["fish"].ready)

        readiness.clear_failures("fish")
        self.assertTrue(_paths(config=config)["fish"].ready)

    def test_cloud_credentials_still_leave_live_voice_usable(self):
        """The end state that matters: some path can always start."""
        with tempfile.TemporaryDirectory() as tmp:
            adc = Path(tmp) / "application_default_credentials.json"
            adc.write_text("{}", encoding="utf-8")
            readiness.remember_failure("fish", "out of credit")
            paths = _paths(
                config=self._config(fish_agent_id="agent-123", ws_url=""),
                adc_candidates=[adc],
            )

        summary = readiness.summary(paths.values())
        self.assertTrue(summary["voice_ready"])
        self.assertIn("gemini_vertex", summary["voice_ready_paths"])


class RetiredLiveModelTestCase(unittest.TestCase):
    """A retired model name must not cost a failed handshake.

    `gemini-2.0-flash-exp` is absent from the Live API's own model listing while
    `gemini-3.8-live` is present there - and it is the model this project runs,
    so that is what a retired name is rewritten to. It also survives in `.env`,
    which is gitignored and outranks `config.yaml`, so rewriting it in the client
    is the only way to repair a checkout that still names it.
    """

    def _client(self, model: str):
        from jarvis.config import LiveVoiceConfig
        from jarvis.live.client import GeminiLiveClient

        return GeminiLiveClient(LiveVoiceConfig(model=model)), model

    def test_a_retired_model_is_replaced(self):
        from jarvis.live.client import RETIRED_LIVE_MODELS

        client, _ = self._client("gemini-2.0-flash-exp")
        candidates = client._model_candidates(True)

        self.assertEqual(candidates[0], "gemini-3.8-live")
        for retired in RETIRED_LIVE_MODELS:
            self.assertNotIn(retired, candidates)

    def test_the_substitution_is_reported_not_silent(self):
        client, _ = self._client("gemini-2.0-flash-exp")
        with patch("jarvis.live.client.log.warn") as warn:
            client._model_candidates(True)

        self.assertTrue(warn.called)
        self.assertIn("gemini-2.0-flash-exp", warn.call_args[0][0])

    def test_a_current_model_is_left_alone(self):
        client, _ = self._client("gemini-3.1-flash-live-preview")
        with patch("jarvis.live.client.log.warn") as warn:
            candidates = client._model_candidates(True)

        self.assertEqual(candidates[0], "gemini-3.1-flash-live-preview")
        self.assertFalse(warn.called)

    def test_an_unknown_model_is_still_tried_first(self):
        """A model this table does not know about may be new, not dead."""
        client, _ = self._client("gemini-9.9-flash-live-preview")
        with patch("jarvis.live.client.log.warn") as warn:
            candidates = client._model_candidates(True)

        self.assertEqual(candidates[0], "gemini-9.9-flash-live-preview")
        self.assertFalse(warn.called)


class CreditHintTestCase(unittest.TestCase):
    """The hint must name a remedy this project can actually satisfy."""

    def test_the_credit_hint_names_a_remedy_that_exists(self):
        from jarvis.live.fish_agents import FishAPIError

        hint = FishAPIError("x", status=402).hint
        self.assertIn("credit", hint)
        self.assertIn("JARVIS_LIVE_API_KEY", hint)
        self.assertIn("gcloud auth application-default login", hint)
        # It used to point at a relay this project does not ship, which made an
        # exhausted account produce two dead ends in a row.
        self.assertNotIn("ws_url", hint)


class VaultKeyPrecedenceTestCase(unittest.TestCase):
    """A stored key must not be ignored just because the backend says gcloud."""

    def test_a_vault_key_outranks_a_gcloud_backend(self):
        from jarvis.config import load_config

        cleaned = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("JARVIS_LIVE_", "GEMINI_", "GOOGLE_"))
        }
        # `load_config` loads `.env` itself, after this test has cleared the
        # environment, so the checkout's own file would otherwise put a key back
        # and the vault would never be consulted.
        with patch.dict(os.environ, cleaned, clear=True), patch(
            "dotenv.load_dotenv",
        ), patch(
            "jarvis.security.get_secret",
            lambda name: "AIzaSyVAULTKEY" if name in {"JARVIS_LIVE_API_KEY", "GEMINI_API_KEY"} else None,
        ):
            cfg = load_config()

        self.assertEqual(cfg.live_voice.api_key, "AIzaSyVAULTKEY")
        self.assertEqual(cfg.live_voice.backend, "api_key")


if __name__ == "__main__":
    unittest.main()
