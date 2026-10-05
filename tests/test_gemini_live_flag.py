"""``python run.py --gemini-live``: one flag that opens the browser on the Gemini
Live voice path, pinned to the Gemini 3.8 live model and a Gemini API key."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import run  # noqa: E402
from jarvis.config import Config  # noqa: E402


def shipped_like_config() -> Config:
    """A config that looks like the one this project ships.

    ``config.yaml`` selects the Fish engine, so a flag that only set values on
    the parent's own config would leave the page on Fish - which is exactly the
    bug these tests exist to catch.
    """
    cfg = Config()
    cfg.live_voice.enabled = False
    cfg.live_voice.provider = "fish"
    cfg.live_voice.model = "gemini-2.0-flash-exp"
    return cfg


class GeminiLiveFlagTests(unittest.TestCase):
    def _run(self, argv: list[str], cfg: Config | None = None):
        """Run the launcher with the browser stubbed and the environment sandboxed.

        ``run_browser`` is the seam: it is what opens the page, so asserting on
        it proves the flag reached the live branch rather than the console REPL.
        The environment is sandboxed because ``--gemini-live`` exports its pins,
        and a leaked ``JARVIS_LIVE_PROVIDER`` would otherwise follow every test
        that runs after this file in the same process. The snapshot is taken
        inside that sandbox, so it shows what the flag actually exported.
        """
        cfg = cfg if cfg is not None else shipped_like_config()
        with (
            patch.dict(os.environ, {}, clear=False),
            patch.object(run, "load_config", return_value=cfg),
            patch("jarvis.browser.run_browser", return_value=0) as browser,
        ):
            result = run.main(argv)
            environment = dict(os.environ)
        return result, cfg, browser, environment

    def test_opens_the_browser_in_live_voice_mode(self):
        result, cfg, browser, _ = self._run(["--gemini-live"])
        self.assertEqual(result, 0)
        self.assertTrue(cfg.live_voice.enabled)
        browser.assert_called_once()
        self.assertTrue(browser.call_args.kwargs["live_voice"])

    def test_pins_provider_model_and_key_backend_over_the_shipped_engine(self):
        _, cfg, _, _ = self._run(["--gemini-live"])
        self.assertEqual(cfg.live_voice.provider, "gemini")
        self.assertEqual(cfg.live_voice.backend, "api_key")
        self.assertEqual(cfg.live_voice.model, "gemini-3.8-live")

    def test_exports_the_pinned_session_for_the_page_and_the_relay(self):
        """The page and the relay re-read the config from the environment.

        ``.env`` outranks ``config.yaml``, and the shipped file selects Fish, so
        without these exports the terminal would announce a Gemini session while
        the browser opened another engine.
        """
        _, _, _, environment = self._run(["--gemini-live"])
        self.assertEqual(environment.get("JARVIS_LIVE_PROVIDER"), "gemini")
        self.assertEqual(environment.get("JARVIS_LIVE_BACKEND"), "api_key")
        self.assertEqual(environment.get("JARVIS_LIVE_MODEL"), "gemini-3.8-live")

    def test_key_flag_sets_the_config_and_is_exported(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GEMINI_API_KEY", None)
            _, cfg, _, environment = self._run(
                ["--gemini-live", "--gemini-live-key", "AQ.example-key"]
            )
            self.assertEqual(cfg.live_voice.api_key, "AQ.example-key")
            self.assertEqual(environment.get("JARVIS_LIVE_API_KEY"), "AQ.example-key")
            # ...and only there: the brain's own credential is not rewritten.
            self.assertNotIn("GEMINI_API_KEY", environment)

    def test_live_model_and_voice_flags_still_override_and_are_exported(self):
        _, cfg, _, environment = self._run([
            "--gemini-live",
            "--live-model", "gemini-3.1-flash-live-preview",
            "--live-voice", "Puck",
        ])
        self.assertEqual(cfg.live_voice.model, "gemini-3.1-flash-live-preview")
        self.assertEqual(cfg.live_voice.voice_name, "Puck")
        self.assertEqual(environment.get("JARVIS_LIVE_MODEL"), "gemini-3.1-flash-live-preview")
        self.assertEqual(environment.get("JARVIS_LIVE_VOICE"), "Puck")

    def test_gemini_live_wins_over_the_plain_browser_flag(self):
        _, _, browser, _ = self._run(["--browser", "--gemini-live"])
        browser.assert_called_once()
        self.assertTrue(browser.call_args.kwargs["live_voice"])

    def test_terminal_live_is_refused(self):
        with self.assertRaises(SystemExit):
            run.main(["--gemini-live", "--terminal-live"])


if __name__ == "__main__":
    unittest.main()
