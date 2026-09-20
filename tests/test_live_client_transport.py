"""The Gemini Live client's transport: credential discovery and task cleanup.

Two defects lived here, both invisible on the machine they were written on:

* Credentials were looked for under Windows' ``%APPDATA%`` only, while the
  readiness report deliberately also checks ``~/.config/gcloud/...``. On
  Linux/macOS the report therefore promised "Vertex ready" and the client then
  found nothing.
* The receiver/sender pair was cancelled but never awaited, so a pump could
  still be mid-send against the socket the ``async with`` was closing.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jarvis.config import LiveVoiceConfig


class _FakeTokenResponse:
    """Stands in for the OAuth token endpoint's HTTP response."""

    def __init__(self, payload: str) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload.encode("utf-8")

    def __enter__(self) -> "_FakeTokenResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def _write_adc(path: Path, project: str = "jarvis-project") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "type": "authorized_user",
                "client_id": "client-id",
                "client_secret": "client-secret",
                "refresh_token": "refresh-token",
                "quota_project_id": project,
            }
        ),
        encoding="utf-8",
    )
    return path


class AdcDiscoveryTestCase(unittest.TestCase):
    """The client must find credentials wherever the readiness report does."""

    def _client(self):
        from jarvis.live.client import GeminiLiveClient

        return GeminiLiveClient(LiveVoiceConfig(backend="gcloud"))

    def _refresh(self, home: Path):
        """Refresh the token for an ADC file living under ``home``."""
        response = _FakeTokenResponse(json.dumps({"access_token": "token-from-adc", "expires_in": 3600}))
        env = {"HOME": str(home), "USERPROFILE": str(home)}
        # `google.auth` is disabled so this can only pass through the direct
        # ADC read: the fallback would otherwise reach the same file and hide a
        # broken fast path.
        with patch.dict(os.environ, env, clear=True), patch.dict(
            sys.modules, {"google.auth": None}
        ), patch("urllib.request.urlopen", return_value=response):
            return self._client()._get_auth_token_and_project()

    def test_credentials_are_found_in_the_posix_location(self):
        """`~/.config/gcloud/...` is the ADC path on Linux and macOS."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            _write_adc(home / ".config" / "gcloud" / "application_default_credentials.json")
            token, project = self._refresh(home)

        self.assertEqual(token, "token-from-adc")
        self.assertEqual(project, "jarvis-project")

    def test_credentials_are_found_in_the_windows_location(self):
        """`%APPDATA%/gcloud/...` keeps working."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            appdata = home / "AppData" / "Roaming"
            adc = _write_adc(appdata / "gcloud" / "application_default_credentials.json")
            response = _FakeTokenResponse(
                json.dumps({"access_token": "token-from-adc", "expires_in": 3600})
            )
            env = {
                "HOME": str(home),
                "USERPROFILE": str(home),
                "APPDATA": str(appdata),
                "GOOGLE_APPLICATION_CREDENTIALS": str(adc),
            }
            with patch.dict(os.environ, env, clear=True), patch.dict(
                sys.modules, {"google.auth": None}
            ), patch("urllib.request.urlopen", return_value=response):
                token, project = self._client()._get_auth_token_and_project()

        self.assertEqual(token, "token-from-adc")
        self.assertEqual(project, "jarvis-project")

    def test_a_missing_credential_file_still_reports_the_remedy(self):
        """No ADC anywhere must raise the actionable error, not a bare failure."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with patch.dict(
                os.environ, {"HOME": str(home), "USERPROFILE": str(home)}, clear=True
            ), patch.dict(sys.modules, {"google.auth": None}), patch(
                "urllib.request.urlopen", side_effect=AssertionError("no token call expected")
            ):
                with self.assertRaises(RuntimeError) as caught:
                    self._client()._get_auth_token_and_project()

        self.assertIn("gcloud auth application-default login", str(caught.exception))


class _FakeSocket:
    """A Live socket that answers one message and records what outlives it."""

    def __init__(self, on_recv) -> None:
        self._on_recv = on_recv
        self.pending_at_close: list[str] = []

    async def send(self, payload: str) -> None:
        return None

    async def recv(self) -> str:
        self._on_recv()
        return json.dumps({"serverContent": {"turnComplete": True}})

    async def close(self) -> None:
        return None

    async def __aenter__(self) -> "_FakeSocket":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        current = asyncio.current_task()
        self.pending_at_close = [
            task.get_name()
            for task in asyncio.all_tasks()
            if task is not current and not task.done()
        ]
        return False


class PumpCleanupTestCase(unittest.TestCase):
    """A session must not close while its pumps are still running."""

    def _run_one_session(self) -> _FakeSocket:
        from jarvis.live.client import GeminiLiveClient

        client = GeminiLiveClient(LiveVoiceConfig(backend="api_key", api_key="test-key"))
        client._is_running = True
        client._outbound_queue = asyncio.Queue()
        socket = _FakeSocket(lambda: setattr(client, "_is_running", False))

        loop = asyncio.new_event_loop()
        try:
            with patch("websockets.connect", return_value=socket):
                loop.run_until_complete(client._run_loop(__import__("threading").Event()))
        finally:
            loop.close()
        return socket

    def test_finished_session_leaves_no_pending_pump_tasks(self):
        socket = self._run_one_session()
        self.assertEqual(
            socket.pending_at_close,
            [],
            "the receiver/sender pumps must be awaited after cancel()",
        )


if __name__ == "__main__":
    unittest.main()
