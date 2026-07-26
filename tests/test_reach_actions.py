"""Tests for the reach-expanding actions: python, http_request, download_file.

These give Jarvis arbitrary computation and first-class web/API access. Each
is wired through schema.py + registry.py; the schema<->registry parity check
in test_new_actions.py already guards the wiring, so here we test behaviour.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from jarvis.tools import files, system


class RunPythonTests(unittest.TestCase):
    def test_empty_code(self):
        self.assertIn("needs code", system.run_python(""))

    def test_prints_result(self):
        out = system.run_python("print(6 * 7)")
        self.assertIn("exit code 0", out)
        self.assertIn("42", out)

    def test_uses_installed_libraries(self):
        out = system.run_python("import json; print(json.dumps({'a': 1}))")
        self.assertIn('{"a": 1}', out)

    def test_error_is_reported_not_raised(self):
        out = system.run_python("raise ValueError('boom')")
        self.assertNotIn("exit code 0", out)
        self.assertIn("boom", out)

    def test_no_output_hint(self):
        out = system.run_python("x = 1 + 1")   # computes but prints nothing
        self.assertIn("no output", out)

    def test_timeout_is_clamped_and_isolated(self):
        # A hang is killed at the timeout instead of freezing Jarvis.
        out = system.run_python("import time; time.sleep(5)", timeout=1)
        self.assertIn("timed out", out)


class HttpRequestTests(unittest.TestCase):
    @staticmethod
    def _resp(text="", status=200, ctype="application/json"):
        r = Mock()
        r.text = text
        r.status_code = status
        r.headers = {"content-type": ctype}
        return r

    @patch("requests.request")
    def test_get_default_method_and_scheme(self, req):
        req.return_value = self._resp('{"ok": true}')
        out = system.http_request("", "api.example.com/status")
        self.assertEqual(req.call_args[0][0], "GET")
        self.assertEqual(req.call_args[0][1], "https://api.example.com/status")
        self.assertIn("HTTP 200", out)
        self.assertIn('{"ok": true}', out)

    @patch("requests.request")
    def test_post_sends_json_body_and_headers(self, req):
        req.return_value = self._resp("created", status=201)
        system.http_request("post", "https://api.example.com/items",
                            headers={"Authorization": "Bearer T"},
                            json_body={"name": "widget"})
        self.assertEqual(req.call_args[0][0], "POST")
        self.assertEqual(req.call_args.kwargs["json"], {"name": "widget"})
        self.assertEqual(req.call_args.kwargs["headers"],
                         {"Authorization": "Bearer T"})

    @patch("requests.request")
    def test_body_truncation(self, req):
        req.return_value = self._resp("x" * 10000)
        out = system.http_request("GET", "https://api.example.com",
                                  max_chars=500)
        self.assertIn("truncated", out)

    @patch("requests.request")
    def test_network_error_is_reported(self, req):
        req.side_effect = RuntimeError("no route to host")
        out = system.http_request("GET", "https://api.example.com")
        self.assertIn("failed", out)
        self.assertIn("no route to host", out)

    def test_empty_url(self):
        self.assertIn("needs a url", system.http_request("GET", ""))


class _StreamResp:
    """A stand-in streaming requests response (context manager + iter_content)."""

    def __init__(self, chunks, status=200):
        self._chunks = chunks
        self.status_code = status
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_content(self, chunk_size=65536):
        yield from self._chunks


class DownloadFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.allow = (str(self.tmp),)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_empty_url(self):
        self.assertIn("needs a url", files.download_file(""))

    def test_refuses_outside_sandbox(self):
        # Must refuse BEFORE any network call happens.
        with patch("requests.get") as get:
            out = files.download_file("https://example.com/x.bin",
                                      dest="C:/Windows/x.bin", allow=())
            get.assert_not_called()
        self.assertIn("refused", out)

    @patch("requests.get")
    def test_downloads_into_folder_using_url_name(self, get):
        get.return_value = _StreamResp([b"abc", b"def"])
        out = files.download_file("https://example.com/data.csv",
                                  dest=str(self.tmp), allow=self.allow)
        self.assertIn("downloaded 6 bytes", out)
        self.assertEqual((self.tmp / "data.csv").read_bytes(), b"abcdef")

    @patch("requests.get")
    def test_http_error_reported(self, get):
        get.return_value = _StreamResp([], status=404)
        out = files.download_file("https://example.com/missing.bin",
                                  dest=str(self.tmp), allow=self.allow)
        self.assertIn("HTTP 404", out)

    @patch("requests.get")
    def test_size_cap_aborts_and_cleans_up(self, get):
        one_mb = b"x" * (1024 * 1024)
        get.return_value = _StreamResp([one_mb, one_mb])   # 2 MB > 1 MB cap
        out = files.download_file("https://example.com/big.bin",
                                  dest=str(self.tmp / "big.bin"),
                                  allow=self.allow, max_mb=1)
        self.assertIn("exceeds the 1 MB limit", out)
        self.assertFalse((self.tmp / "big.bin").exists())   # partial removed

    @patch("requests.get")
    def test_no_overwrite(self, get):
        (self.tmp / "here.bin").write_bytes(b"old")
        out = files.download_file("https://example.com/here.bin",
                                  dest=str(self.tmp / "here.bin"),
                                  allow=self.allow)
        self.assertIn("already exists", out)
        get.assert_not_called()


class ExposureTests(unittest.TestCase):
    """The new actions must be reachable where they matter."""

    def test_coder_can_compute_and_reach_the_web(self):
        from jarvis.agent.coder import ALLOWED
        for a in ("python", "http_request", "download_file"):
            self.assertIn(a, ALLOWED)

    def test_headless_subagents_get_them(self):
        from jarvis.agent.subagent import HEADLESS
        for a in ("python", "http_request", "download_file"):
            self.assertIn(a, HEADLESS)


if __name__ == "__main__":
    unittest.main()
