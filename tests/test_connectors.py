"""Tests for the Gmail/Discord/WhatsApp connectors.

Nothing here touches the network: the provider functions are swapped out and
only the surrounding logic (dispatch, caching, parsing, prompt note, schema
parity) is exercised.
"""

from __future__ import annotations

import email
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.tools import connectors                     # noqa: E402
from jarvis.tools.registry import _HANDLERS             # noqa: E402
from jarvis.tools.schema import ACTIONS_BY_NAME         # noqa: E402

_NO_ENV = {"GMAIL_ADDRESS": "", "GMAIL_APP_PASSWORD": "", "DISCORD_BOT_TOKEN": "",
           "WHATSAPP_TOKEN": "", "WHATSAPP_PHONE_ID": "", "WHATSAPP_INBOX": ""}


class TestDispatch(unittest.TestCase):
    def setUp(self):
        connectors.invalidate()

    def test_action_is_in_schema_and_registry(self):
        self.assertIn("connector", ACTIONS_BY_NAME)
        self.assertIn("connector", _HANDLERS)

    def test_unknown_service_names_the_valid_ones(self):
        with self.assertRaises(connectors.ConnectorError) as ctx:
            connectors.fetch("telegram", "unread")
        self.assertIn("gmail", str(ctx.exception))

    def test_aliases_resolve(self):
        calls = []
        with mock.patch.dict(connectors._SERVICES["gmail"],
                             {"fn": lambda *a: calls.append(a) or "ok"}):
            self.assertEqual(connectors.fetch("email", "unread"), "ok")
        self.assertEqual(len(calls), 1)

    def test_limit_is_clamped_and_coerced(self):
        seen = []
        with mock.patch.dict(connectors._SERVICES["gmail"],
                             {"fn": lambda op, q, t, lim: seen.append(lim) or "ok"}):
            connectors.fetch("gmail", "unread", limit=999)
            connectors.fetch("gmail", "unread", limit="bogus")
        self.assertEqual(seen, [50, 10])

    def test_unconfigured_service_explains_what_is_missing(self):
        with mock.patch.dict("os.environ", _NO_ENV):
            with self.assertRaises(connectors.ConnectorError) as ctx:
                connectors.fetch("gmail", "unread")
            self.assertIn("GMAIL_APP_PASSWORD", str(ctx.exception))

    def test_long_results_are_capped(self):
        with mock.patch.dict(connectors._SERVICES["gmail"],
                             {"fn": lambda *a: "x" * 99999}):
            out = connectors.fetch("gmail", "unread")
        self.assertLess(len(out), connectors._CAP + 100)


class TestCache(unittest.TestCase):
    """The whole point of the module is speed, so the cache has to actually
    stop the second call from hitting the provider."""

    def setUp(self):
        connectors.invalidate()

    def test_repeat_call_does_not_reach_the_provider(self):
        calls = []
        with mock.patch.dict(connectors._SERVICES["gmail"],
                             {"fn": lambda *a: calls.append(a) or "inbox"}):
            self.assertEqual(connectors.fetch("gmail", "unread"), "inbox")
            self.assertEqual(connectors.fetch("gmail", "unread"), "inbox")
        self.assertEqual(len(calls), 1)

    def test_different_args_are_cached_separately(self):
        calls = []
        with mock.patch.dict(connectors._SERVICES["gmail"],
                             {"fn": lambda *a: calls.append(a) or "x"}):
            connectors.fetch("gmail", "search", query="a")
            connectors.fetch("gmail", "search", query="b")
        self.assertEqual(len(calls), 2)

    def test_invalidate_forces_a_refetch(self):
        calls = []
        with mock.patch.dict(connectors._SERVICES["gmail"],
                             {"fn": lambda *a: calls.append(a) or "x"}):
            connectors.fetch("gmail", "unread")
            connectors.invalidate("gmail")
            connectors.fetch("gmail", "unread")
        self.assertEqual(len(calls), 2)

    def test_zero_ttl_disables_caching(self):
        calls = []
        with mock.patch.dict("os.environ", {"JARVIS_CONNECTOR_TTL": "0"}), \
             mock.patch.dict(connectors._SERVICES["gmail"],
                             {"fn": lambda *a: calls.append(a) or "x"}):
            connectors.fetch("gmail", "unread")
            connectors.fetch("gmail", "unread")
        self.assertEqual(len(calls), 2)


class TestGmailParsing(unittest.TestCase):
    def test_encoded_subject_is_decoded(self):
        msg = email.message_from_string(
            "From: a@b.c\nSubject: =?utf-8?B?SGVsbG8gd29ybGQ=?=\n\n")
        self.assertEqual(connectors._hdr(msg, "Subject"), "Hello world")

    def test_plain_header_survives(self):
        msg = email.message_from_string("From: Bob <b@x.io>\n\n")
        self.assertEqual(connectors._hdr(msg, "From"), "Bob <b@x.io>")

    def test_missing_header_is_empty(self):
        msg = email.message_from_string("From: a@b.c\n\n")
        self.assertEqual(connectors._hdr(msg, "Subject"), "")


class _FakeIMAP:
    """Just enough imaplib surface to exercise the SEARCH/FETCH parsing."""

    def __init__(self, uids=(b"101", b"102", b"103"), body=None):
        self.uids = list(uids)
        self.body = body
        self.selected = None
        self.readonly = None

    def select(self, mailbox, readonly=False):
        self.selected, self.readonly = mailbox, readonly
        return "OK", [b"3"]

    def uid(self, command, *args):
        if command == "SEARCH":
            return "OK", [b" ".join(self.uids)]
        if self.body is not None:                       # a 'read' fetch
            return "OK", [(b"1 (UID 101 BODY[] {10}", self.body), b")"]
        parts = []
        for uid in args[0].encode().split(b","):        # a header fetch
            headers = (b"From: Sender " + uid + b" <s@x.io>\r\n"
                       b"Subject: Subject " + uid + b"\r\n"
                       b"Date: Mon, 1 Jan 2026 09:00:00 +0000\r\n\r\n")
            parts.append((b"1 (UID " + uid + b" BODY[HEADER.FIELDS ...] {90}",
                          headers))
            parts.append(b")")
        return "OK", parts


class TestGmailFetch(unittest.TestCase):
    def setUp(self):
        connectors.invalidate()

    def _run(self, op, **kw):
        fake = _FakeIMAP(**kw.pop("fake", {}))
        with mock.patch.object(connectors, "_gmail_login", return_value=fake):
            return connectors.fetch("gmail", op, **kw), fake

    def test_unread_listing_is_newest_first(self):
        out, fake = self._run("unread")
        self.assertLess(out.index("[103]"), out.index("[101]"))
        self.assertIn("Subject 102", out)

    def test_reading_the_inbox_never_marks_mail_as_seen(self):
        _, fake = self._run("unread")
        self.assertEqual(fake.selected, "INBOX")
        self.assertTrue(fake.readonly)

    def test_empty_inbox_says_so(self):
        out, _ = self._run("unread", fake={"uids": ()})
        self.assertEqual(out, "no unread mail")

    def test_limit_trims_to_the_newest(self):
        out, _ = self._run("unread", limit=2)
        self.assertNotIn("[101]", out)
        self.assertIn("[103]", out)

    def test_search_requires_a_query(self):
        with self.assertRaises(connectors.ConnectorError) as ctx:
            self._run("search")
        self.assertIn("query", str(ctx.exception))

    def test_read_returns_the_plain_text_body(self):
        raw = (b"From: bob@x.io\r\nSubject: Report\r\n"
               b"Content-Type: text/plain; charset=utf-8\r\n\r\nthe body text\r\n")
        out, _ = self._run("read", target="101", fake={"body": raw})
        self.assertIn("the body text", out)
        self.assertIn("Report", out)

    def test_read_falls_back_to_stripped_html(self):
        raw = (b"From: bob@x.io\r\nSubject: Newsletter\r\n"
               b"Content-Type: text/html; charset=utf-8\r\n\r\n"
               b"<html><body><p>hello from html</p></body></html>\r\n")
        out, _ = self._run("read", target="101", fake={"body": raw})
        self.assertIn("hello from html", out)

    def test_read_without_a_target_explains_where_ids_come_from(self):
        with self.assertRaises(connectors.ConnectorError) as ctx:
            self._run("read")
        self.assertIn("listing", str(ctx.exception))


class TestWhatsAppInbox(unittest.TestCase):
    def setUp(self):
        connectors.invalidate()

    def _inbox(self, lines):
        import tempfile
        fh = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False,
                                         encoding="utf-8")
        fh.write("\n".join(json.dumps(x) for x in lines))
        fh.close()
        self.addCleanup(lambda: Path(fh.name).unlink(missing_ok=True))
        return fh.name

    def test_reads_flat_records(self):
        path = self._inbox([{"from": "919", "text": "hi there",
                             "timestamp": "1700000000"}])
        with mock.patch.dict("os.environ", {"WHATSAPP_INBOX": path}):
            out = connectors.fetch("whatsapp", "messages")
        self.assertIn("hi there", out)
        self.assertIn("919", out)

    def test_reads_raw_webhook_payloads(self):
        payload = {"entry": [{"changes": [{"value": {"messages": [
            {"from": "4477", "text": {"body": "from webhook"},
             "timestamp": "1700000001"}]}}]}]}
        path = self._inbox([payload])
        with mock.patch.dict("os.environ", {"WHATSAPP_INBOX": path}):
            out = connectors.fetch("whatsapp", "messages")
        self.assertIn("from webhook", out)

    def test_query_filters_messages(self):
        path = self._inbox([{"from": "1", "text": "keep me"},
                            {"from": "2", "text": "drop me"}])
        with mock.patch.dict("os.environ", {"WHATSAPP_INBOX": path}):
            out = connectors.fetch("whatsapp", "messages", query="keep")
        self.assertIn("keep me", out)
        self.assertNotIn("drop me", out)

    def test_corrupt_lines_are_skipped(self):
        import tempfile
        fh = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False,
                                         encoding="utf-8")
        fh.write('not json\n{"from":"1","text":"good"}\n')
        fh.close()
        self.addCleanup(lambda: Path(fh.name).unlink(missing_ok=True))
        with mock.patch.dict("os.environ", {"WHATSAPP_INBOX": fh.name}):
            out = connectors.fetch("whatsapp", "messages")
        self.assertIn("good", out)

    def test_no_inbox_configured_explains_the_webhook_requirement(self):
        with mock.patch.dict("os.environ", _NO_ENV):
            with self.assertRaises(connectors.ConnectorError) as ctx:
                connectors.fetch("whatsapp", "messages")
        self.assertIn("webhook", str(ctx.exception).lower())


class TestPromptNote(unittest.TestCase):
    def test_note_is_empty_when_nothing_is_configured(self):
        with mock.patch.dict("os.environ", _NO_ENV):
            self.assertEqual(connectors.note(), "")

    def test_note_lists_a_configured_service_only(self):
        with mock.patch.dict("os.environ", dict(_NO_ENV, GMAIL_ADDRESS="a@b.c",
                                                GMAIL_APP_PASSWORD="pw")):
            note = connectors.note()
        self.assertIn("gmail", note)
        self.assertNotIn("discord ->", note)

    def test_whatsapp_counts_as_configured_with_an_inbox_alone(self):
        with mock.patch.dict("os.environ", dict(_NO_ENV, WHATSAPP_INBOX="x.jsonl")):
            self.assertTrue(connectors.configured("whatsapp"))

    def test_status_names_the_missing_variables(self):
        with mock.patch.dict("os.environ", _NO_ENV):
            report = connectors.status()
        self.assertIn("DISCORD_BOT_TOKEN", report)
        self.assertIn("not set up", report)


class TestRegistryHandler(unittest.TestCase):
    def setUp(self):
        connectors.invalidate()

    def test_handler_returns_the_error_instead_of_raising(self):
        handler = _HANDLERS["connector"]
        with mock.patch.dict("os.environ", _NO_ENV):
            result = handler({"service": "gmail", "op": "unread"}, None, None)
        self.assertFalse(result.ok)
        self.assertFalse(result.needs_observe)      # never costs a screenshot
        self.assertIn("GMAIL_ADDRESS", result.message)

    def test_handler_requires_a_service(self):
        result = _HANDLERS["connector"]({}, None, None)
        self.assertFalse(result.ok)
        self.assertIn("service", result.message)

    def test_handler_passes_args_through(self):
        with mock.patch.dict(connectors._SERVICES["discord"],
                             {"fn": lambda op, q, t, lim: f"{op}|{t}|{lim}"}):
            result = _HANDLERS["connector"](
                {"service": "discord", "op": "messages", "target": "#general",
                 "limit": 3}, None, None)
        self.assertTrue(result.ok)
        self.assertEqual(result.message, "messages|#general|3")


if __name__ == "__main__":
    unittest.main()
