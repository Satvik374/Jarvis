"""Editing credentials without editing a text file.

The API-keys tab exists because hand-editing ``.env`` is the only way Jarvis has
ever accepted a key: the file is long, the names are not the product names, and
one misspelling turns a feature off silently. What is pinned here is the part
that has to be right for that convenience not to be a liability:

* the file survives the edit - comments, order, trailing comments, newline style
  and lines nobody touched;
* a *secret* never travels back to the page, so a page that can save a key still
  cannot read one;
* a save reaches the running process (the vault mirror) and says so, instead of
  reporting success for something that only applies after a restart;
* and the test sandbox can never write the real ``.env``. ``.env`` is
  deliberately not one of the relocatable state locations, so a test that drove
  the default path would be addressed at the developer's own credentials.

The routes are driven through the real ``BrowserRequestHandler`` with its real
token check, because "the endpoint exists" and "the endpoint is authorized" are
different claims and only the second one matters.
"""

from __future__ import annotations

import inspect
import json
import os
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.browser import STATIC_DIR, BrowserRequestHandler
from jarvis.security import api_keys
from jarvis.utils import paths as jarvis_paths

INDEX_HTML = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
STYLES_CSS = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")
APP_JS = (STATIC_DIR / "app.js").read_text(encoding="utf-8")


class FakeVault:
    """The credential vault, without the machine's real credential store."""

    def __init__(self, values=None, fail=False):
        self.values = {k.upper(): v for k, v in (values or {}).items()}
        self.fail = fail
        self.deleted: list[str] = []

    def get_secret(self, key, default=None):
        return self.values.get(key.strip().upper(), default)

    def set_secret(self, key, secret, backend="credman"):
        if self.fail or not str(secret).strip():
            return False
        self.values[key.strip().upper()] = str(secret).strip()
        return True

    def delete_secret(self, key):
        name = key.strip().upper()
        self.deleted.append(name)
        return self.values.pop(name, None) is not None

    def list_secrets(self):
        return [
            {"key": name, "backend": "dpapi", "masked": "****"}
            for name in self.values
        ]


@pytest.fixture
def env_file(tmp_path) -> Path:
    """A CRLF ``.env`` with comments, a trailing comment and a stray key."""
    path = tmp_path / ".env"
    path.write_bytes(
        b"# Jarvis credentials\r\n"
        b"BACKEND=openrouter\r\n"
        b"\r\n"
        b"# The gateway key\r\n"
        b"OPENROUTER_API_KEY=sk-or-existing\r\n"
        b"JARVIS_VISION=0   # text-only model\r\n"
        b"MY_OWN_FLAG=yes\r\n"
    )
    return path


@pytest.fixture
def clean_env(monkeypatch):
    """No inherited value for any documented variable.

    The suite shares one process, and a test that ran ``load_config()`` earlier
    has already loaded the developer's ``.env`` into ``os.environ`` - which would
    make "is this key set" answer differently depending on test order.
    """
    for spec in api_keys.CATALOGUE:
        monkeypatch.delenv(spec.name, raising=False)


# --------------------------------------------------------------------------- #
#  The catalogue
# --------------------------------------------------------------------------- #


def test_every_documented_variable_is_described_once():
    names = [spec.name for spec in api_keys.CATALOGUE]
    assert len(names) == len(set(names))
    assert all(api_keys._NAME_RE.match(name) for name in names)
    assert api_keys.SPECS == {spec.name: spec for spec in api_keys.CATALOGUE}
    assert all(spec.group and spec.label for spec in api_keys.CATALOGUE)


def test_the_catalogue_names_the_variables_the_code_actually_reads():
    """A row that saves a key nothing reads is worse than no row at all."""
    required = {
        "OMNIROUTE_API_KEY",
        "OPENROUTER_API_KEY",
        "GEMINI_API_KEY",
        "JARVIS_LIVE_API_KEY",
        "FISH_AUDIO_API_KEY",
        "AZURE_SPEECH_KEY",
        "GMAIL_ADDRESS",
        "GMAIL_APP_PASSWORD",
        "DISCORD_BOT_TOKEN",
        "WHATSAPP_TOKEN",
        "JARVIS_REMOTE_URL",
    }
    assert required <= set(api_keys.SPECS)

    # config.yaml names its own key variable, and the tab has to offer that one.
    config_yaml = (Path(jarvis_paths.project_root()) / "config.yaml").read_text(
        encoding="utf-8"
    )
    declared = [
        line.split(":", 1)[1].strip()
        for line in config_yaml.splitlines()
        if line.strip().startswith("api_key_env:")
    ]
    assert declared, "config.yaml no longer declares api_key_env"
    for name in declared:
        assert name in api_keys.SPECS, f"config.yaml reads {name}, the tab does not offer it"


def test_only_plain_settings_are_reported_verbatim():
    plain = {spec.name for spec in api_keys.CATALOGUE if not spec.secret}
    assert {"BACKEND", "MODEL_ID", "JARVIS_VISION"} <= plain
    # Anything that authenticates as somebody must never be in that set.
    assert not plain & {
        "OPENROUTER_API_KEY",
        "GEMINI_API_KEY",
        "OMNIROUTE_API_KEY",
        "DISCORD_BOT_TOKEN",
        "GMAIL_APP_PASSWORD",
        "WHATSAPP_TOKEN",
        "AZURE_SPEECH_KEY",
        "FISH_AUDIO_API_KEY",
        "JARVIS_LIVE_API_KEY",
    }


# --------------------------------------------------------------------------- #
#  Reading
# --------------------------------------------------------------------------- #


def test_reading_the_file_understands_what_dotenv_understands():
    lines = [
        "# comment",
        "",
        "PLAIN=value",
        'export EXPORTED="quoted value"',
        "EMPTY=",
        "SPACED = padded ",
        "FIRST=1",
        "FIRST=2",
        "NOT AN ASSIGNMENT",
    ]
    values = api_keys.parse(lines)
    assert values["PLAIN"] == "value"
    assert values["EXPORTED"] == "quoted value"
    assert values["EMPTY"] == ""
    assert values["SPACED"] == "padded"
    assert values["FIRST"] == "2", "later assignments win, as in python-dotenv"
    assert "NOT AN ASSIGNMENT" not in values


def test_a_secret_is_described_without_being_revealed(env_file, clean_env):
    report = api_keys.describe(env_file, FakeVault())
    entry = next(item for item in report["keys"] if item["key"] == "OPENROUTER_API_KEY")

    assert entry["set"] is True
    assert entry["source"] == "file"
    assert entry["masked"] == "sk-o\u2022\u2022\u2022\u2022ting"
    # The length is what tells a truncated paste from a whole key without ever
    # moving the key itself.
    assert entry["length"] == len("sk-or-existing")
    assert "value" not in entry, "a secret must not travel in full"
    assert "sk-or-existing" not in json.dumps(report), "the mask leaked the real value"


def test_masking_keeps_the_ends_and_hides_short_secrets():
    assert api_keys._mask("sk-or-v1-abcdefghijklmnop") == "sk-o\u2022\u2022\u2022\u2022mnop"
    assert api_keys._mask("short") == "\u2022" * 8
    assert api_keys._mask("") == ""


def test_a_plain_setting_is_reported_verbatim(env_file, clean_env):
    report = api_keys.describe(env_file, FakeVault())
    entry = next(item for item in report["keys"] if item["key"] == "BACKEND")
    assert entry["value"] == "openrouter"
    assert entry["masked"] == "", "a setting nobody authenticates with is not masked"


def test_the_source_says_where_a_value_is_kept(tmp_path, clean_env, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("IN_THE_FILE=1\n", encoding="utf-8")
    monkeypatch.setenv("BACKEND", "ollama")
    vault = FakeVault({"GEMINI_API_KEY": "AIza-vault-copy"})

    report = api_keys.describe(path, vault)
    sources = {item["key"]: item["source"] for item in report["keys"]}
    assert sources["OPENROUTER_API_KEY"] == ""       # nothing anywhere
    assert sources["BACKEND"] == "environment"       # os.environ only
    assert sources["GEMINI_API_KEY"] == "vault"      # the vault, not the file


def test_a_variable_the_catalogue_does_not_know_is_still_shown(env_file, clean_env):
    report = api_keys.describe(env_file, FakeVault())
    entry = next(item for item in report["keys"] if item["key"] == "MY_OWN_FLAG")
    assert entry["custom"] is True
    assert entry["group"] == api_keys.DISCOVERED
    assert entry["set"] is True


def test_a_credential_that_lives_only_in_the_vault_is_shown(tmp_path, clean_env):
    """:secret set leaves no trace in .env, which is exactly why it gets lost."""
    report = api_keys.describe(tmp_path / ".env", FakeVault({"SOME_TOKEN": "abc123"}))
    entry = next(item for item in report["keys"] if item["key"] == "SOME_TOKEN")
    assert entry["group"] == api_keys.VAULT_ONLY
    assert entry["source"] == "vault"
    assert entry["custom"] is True


def test_the_report_counts_what_is_configured(env_file, clean_env):
    report = api_keys.describe(env_file, FakeVault())
    assert report["total"] == len(report["keys"])
    assert report["configured"] == sum(1 for item in report["keys"] if item["set"])
    assert report["env_path"] == str(env_file)
    assert report["groups"][0] == api_keys.BRAIN


# --------------------------------------------------------------------------- #
#  Writing
# --------------------------------------------------------------------------- #


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_writing_keeps_comments_order_and_untouched_lines(env_file, clean_env):
    api_keys.apply({"OPENROUTER_API_KEY": "sk-or-new"}, path=env_file, vault=FakeVault())
    lines = _text(env_file).splitlines()

    assert "# Jarvis credentials" in lines
    assert "# The gateway key" in lines
    assert "OPENROUTER_API_KEY=sk-or-new" in lines
    assert "BACKEND=openrouter" in lines
    assert "MY_OWN_FLAG=yes" in lines
    # Rewritten in place: the file's ordering is what makes it readable.
    assert lines.index("OPENROUTER_API_KEY=sk-or-new") == 4


def test_a_trailing_comment_survives_a_value_change(env_file, clean_env):
    api_keys.apply({"JARVIS_VISION": "1"}, path=env_file, vault=FakeVault())
    assert "JARVIS_VISION=1   # text-only model" in _text(env_file)


def test_a_value_that_needs_quoting_is_quoted_and_read_back(env_file, clean_env):
    value = 'key with "quotes" and spaces'
    api_keys.apply({"GMAIL_APP_PASSWORD": value}, path=env_file, vault=FakeVault())
    written = _text(env_file)
    assert '"key with \\"quotes\\" and spaces"' in written
    lines, _ = api_keys._read(env_file)
    assert api_keys.parse(lines)["GMAIL_APP_PASSWORD"] == value


def test_a_cleared_key_keeps_its_line_and_loses_its_value(env_file, clean_env):
    report = api_keys.apply({"OPENROUTER_API_KEY": ""}, path=env_file, vault=FakeVault())
    assert report["cleared"] == ["OPENROUTER_API_KEY"]
    assert "OPENROUTER_API_KEY=" in _text(env_file)
    assert "sk-or-existing" not in _text(env_file)
    entry = next(
        item for item in report["keys"] if item["key"] == "OPENROUTER_API_KEY"
    )
    assert entry["set"] is False


def test_removing_a_key_drops_its_line_entirely(env_file, clean_env):
    report = api_keys.apply(remove=["MY_OWN_FLAG"], path=env_file, vault=FakeVault())
    assert report["removed"] == ["MY_OWN_FLAG"]
    assert "MY_OWN_FLAG" not in _text(env_file)


def test_a_new_key_is_appended_once_under_one_header(tmp_path, clean_env):
    path = tmp_path / ".env"
    path.write_text("BACKEND=ollama\n", encoding="utf-8")
    api_keys.apply({"FIRST_TOKEN": "a"}, path=path, vault=FakeVault())
    api_keys.apply({"SECOND_TOKEN": "b"}, path=path, vault=FakeVault())

    text = _text(path)
    assert text.count(api_keys._ADDED_HEADER) == 1
    assert "FIRST_TOKEN=a" in text
    assert "SECOND_TOKEN=b" in text
    # And rewriting an existing key does not append a copy of it.
    api_keys.apply({"BACKEND": "openrouter"}, path=path, vault=FakeVault())
    assert _text(path).count("BACKEND=") == 1


def test_a_name_assigned_twice_is_saved_to_the_line_that_wins(tmp_path, clean_env):
    """The live report's own .env assigns JARVIS_VISION twice. A save must change
    the line that decides the value and leave the dead one exactly as written -
    rewriting both would edit a line the user never asked to touch."""
    path = tmp_path / ".env"
    path.write_bytes(b"GROQ_API_KEY=old\n# note\nGROQ_API_KEY=older\n")
    api_keys.apply({"GROQ_API_KEY": "new"}, path=path, vault=FakeVault())

    assert path.read_bytes() == b"GROQ_API_KEY=old\n# note\nGROQ_API_KEY=new\n"
    lines, _ = api_keys._read(path)
    assert api_keys.parse(lines)["GROQ_API_KEY"] == "new", "the effective value moved"


def test_clearing_a_name_assigned_twice_clears_the_effective_value(tmp_path, clean_env):
    path = tmp_path / ".env"
    path.write_bytes(b"GROQ_API_KEY=old\nGROQ_API_KEY=older\n")
    report = api_keys.apply({"GROQ_API_KEY": ""}, path=path, vault=FakeVault())

    assert path.read_bytes() == b"GROQ_API_KEY=old\nGROQ_API_KEY=\n"
    entry = next(item for item in report["keys"] if item["key"] == "GROQ_API_KEY")
    assert entry["set"] is False


def test_removing_a_name_drops_every_line_for_it(tmp_path, clean_env):
    """A removal means the variable is gone, not that its first line is gone."""
    path = tmp_path / ".env"
    path.write_bytes(b"GROQ_API_KEY=old\nOTHER=1\nGROQ_API_KEY=older\n")
    api_keys.apply(remove=["GROQ_API_KEY"], path=path, vault=FakeVault())
    assert path.read_bytes() == b"OTHER=1\n"


def test_the_files_newline_style_is_kept(env_file, clean_env):
    assert b"\r\n" in env_file.read_bytes()
    api_keys.apply({"NEW_KEY": "1"}, path=env_file, vault=FakeVault())
    raw = env_file.read_bytes()
    assert raw.count(b"\n") == raw.count(b"\r\n"), "bare-LF lines were introduced"

    # Written as bytes: write_text would translate the LF on Windows and the
    # comparison would be against a file that was CRLF all along.
    unix = env_file.parent / "unix.env"
    unix.write_bytes(b"BACKEND=ollama\n")
    api_keys.apply({"NEW_KEY": "1"}, path=unix, vault=FakeVault())
    assert b"\r\n" not in unix.read_bytes()


def test_a_missing_file_is_created(tmp_path, clean_env):
    path = tmp_path / "nested" / ".env"
    report = api_keys.apply({"GROQ_API_KEY": "gsk-1"}, path=path, vault=FakeVault())
    assert report["ok"] is True
    assert "GROQ_API_KEY=gsk-1" in _text(path)


@pytest.mark.parametrize(
    "updates, remove",
    [
        ({"lower case": "x"}, []),
        ({"HAS-DASH": "x"}, []),
        ({"9LEADING": "x"}, []),
        ({"A" * 80: "x"}, []),
        ({"GOOD_NAME": "x" * (api_keys.MAX_VALUE_LENGTH + 1)}, []),
        ({"GOOD_NAME": "line\nbreak"}, []),
        ({f"KEY_{index}": "x" for index in range(api_keys.MAX_UPDATES + 1)}, []),
        ({}, ["not a name"]),
    ],
)
def test_a_bad_request_is_refused(env_file, clean_env, updates, remove):
    report = api_keys.apply(updates, remove, path=env_file, vault=FakeVault())
    assert report["ok"] is False
    assert report["error"]
    assert _text(env_file) == (
        "# Jarvis credentials\n"
        "BACKEND=openrouter\n"
        "\n"
        "# The gateway key\n"
        "OPENROUTER_API_KEY=sk-or-existing\n"
        "JARVIS_VISION=0   # text-only model\n"
        "MY_OWN_FLAG=yes\n"
    ), "a refused request must not touch the file"


def test_an_empty_request_is_not_a_write(env_file, clean_env):
    report = api_keys.apply({}, [], path=env_file, vault=FakeVault())
    assert report["ok"] is False
    assert "nothing to save" in report["error"]


def test_the_running_process_is_told_immediately(env_file, clean_env, monkeypatch):
    """load_config already happened by the time the page is up: os.getenv readers
    in this process must not have to wait for a restart to see the new value."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    api_keys.apply({"GROQ_API_KEY": "gsk-live"}, path=env_file, vault=FakeVault())
    assert os.environ["GROQ_API_KEY"] == "gsk-live"

    api_keys.apply({"GROQ_API_KEY": ""}, path=env_file, vault=FakeVault())
    assert "GROQ_API_KEY" not in os.environ


def test_the_vault_copy_is_what_makes_a_save_apply_without_a_restart(
    env_file, clean_env
):
    vault = FakeVault()
    report = api_keys.apply({"GROQ_API_KEY": "gsk-live"}, path=env_file, vault=vault)
    assert report["vault_stored"] == ["GROQ_API_KEY"]
    assert report["applied_live"] is True
    assert vault.values["GROQ_API_KEY"] == "gsk-live"

    # A machine without DPAPI cannot mirror, and the response has to say so
    # rather than let the page promise something that is not true yet.
    broken = api_keys.apply(
        {"GROQ_API_KEY": "gsk-live"}, path=env_file, vault=FakeVault(fail=True)
    )
    assert broken["applied_live"] is False
    assert broken["vault_stored"] == []


def test_clearing_a_key_also_removes_the_vault_copy(env_file, clean_env):
    vault = FakeVault({"GROQ_API_KEY": "gsk-live"})
    api_keys.apply({"GROQ_API_KEY": ""}, path=env_file, vault=vault)
    assert "GROQ_API_KEY" not in vault.values
    assert "GROQ_API_KEY" in vault.deleted

    api_keys.apply(remove=["MY_OWN_FLAG"], path=env_file, vault=vault)
    assert "MY_OWN_FLAG" in vault.deleted


def test_a_save_reports_the_fresh_state_in_the_same_response(env_file, clean_env):
    report = api_keys.apply(
        {"GROQ_API_KEY": "gsk-live", "MY_OWN_FLAG": ""}, path=env_file, vault=FakeVault()
    )
    assert report["ok"] is True
    assert report["saved"] == ["GROQ_API_KEY"]
    assert report["cleared"] == ["MY_OWN_FLAG"]
    assert report["previous"] == {"GROQ_API_KEY": False, "MY_OWN_FLAG": True}
    # The fresh state travels with the answer, so the page re-renders without a
    # second round trip and without guessing what it now holds.
    assert report["keys"] and all("key" in item for item in report["keys"])


def test_the_sandbox_refuses_to_write_the_real_env_file(tmp_path):
    """`.env` is not a relocatable state location, so the default path is the
    developer's own credentials. Nothing may write it from inside the suite."""
    assert os.environ.get("JARVIS_STATE_DIR"), "the isolation fixture is not active"
    assert jarvis_paths.sandbox_root() is not None

    report = api_keys.apply({"GROQ_API_KEY": "gsk-should-not-land"}, vault=FakeVault())
    assert report["ok"] is False
    assert "sandbox" in report["error"]


def test_summary_lists_the_documented_keys_that_are_still_unset(env_file, clean_env):
    report = api_keys.describe(env_file, FakeVault())
    summary = api_keys.summary(env_file, FakeVault())

    assert summary["env_path"] == str(env_file)
    assert summary["unset"] == sorted(
        entry["key"]
        for entry in report["keys"]
        if not entry["set"] and not entry["custom"]
    )
    assert "OPENROUTER_API_KEY" not in summary["unset"]
    assert "GEMINI_API_KEY" in summary["unset"]


# --------------------------------------------------------------------------- #
#  The HTTP surface
# --------------------------------------------------------------------------- #


def _page(headers):
    """The real handler, with its real token and origin checks in place."""
    handler = object.__new__(BrowserRequestHandler)
    handler.path = "/api/keys"
    handler.headers = headers
    handler.server = SimpleNamespace(
        token="testtoken", origin="http://127.0.0.1:8765", bridge=SimpleNamespace()
    )
    responses: list = []
    handler._json = lambda status, body: responses.append((status, body))
    return handler, responses


def test_the_page_has_to_hold_the_token(env_file, monkeypatch, clean_env):
    monkeypatch.setattr(api_keys, "env_path", lambda: env_file)
    monkeypatch.setattr(api_keys, "_vault", lambda: FakeVault())
    handler, responses = _page({})
    handler._handle_keys()
    assert responses == [
        (HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
    ]

    handler, responses = _page({"X-Jarvis-Token": "testtoken"})
    handler._handle_keys()
    assert responses[0][0] == HTTPStatus.OK
    assert responses[0][1]["configured"] > 0


def test_the_page_is_told_what_is_set_without_being_told_what_it_is(
    env_file, monkeypatch, clean_env
):
    monkeypatch.setattr(api_keys, "env_path", lambda: env_file)
    monkeypatch.setattr(api_keys, "_vault", lambda: FakeVault())
    handler, responses = _page({"X-Jarvis-Token": "testtoken"})
    handler._handle_keys()
    body = responses[0][1]
    assert body["keys"][0]["key"]
    assert "sk-or-existing" not in json.dumps(body)
    entry = next(item for item in body["keys"] if item["key"] == "OPENROUTER_API_KEY")
    assert entry["set"] is True and entry["masked"].startswith("sk-o")


def test_saving_and_clearing_from_the_page_both_write_the_file(
    env_file, monkeypatch, clean_env
):
    monkeypatch.setattr(api_keys, "env_path", lambda: env_file)
    monkeypatch.setattr(api_keys, "_vault", lambda: FakeVault())
    handler, responses = _page(
        {"X-Jarvis-Token": "testtoken", "Origin": "http://127.0.0.1:8765"}
    )

    handler._handle_keys_update({"updates": {"GROQ_API_KEY": "gsk-page"}})
    assert responses[-1][0] == HTTPStatus.OK
    assert "GROQ_API_KEY=gsk-page" in _text(env_file)

    handler._handle_keys_update({"updates": {"GROQ_API_KEY": ""}})
    assert responses[-1][0] == HTTPStatus.OK
    assert "gsk-page" not in _text(env_file)


def test_a_wrong_origin_cannot_save(env_file, monkeypatch, clean_env):
    monkeypatch.setattr(api_keys, "env_path", lambda: env_file)
    handler, responses = _page(
        {"X-Jarvis-Token": "testtoken", "Origin": "http://evil.example"}
    )
    handler._handle_keys_update({"updates": {"GROQ_API_KEY": "nope"}})
    assert responses == [(HTTPStatus.FORBIDDEN, {"ok": False, "error": "invalid origin"})]
    assert "nope" not in _text(env_file)


@pytest.mark.parametrize(
    "payload",
    [
        {"updates": ["GROQ_API_KEY"]},
        {"remove": "GROQ_API_KEY"},
        {"updates": {"bad name": "x"}},
    ],
)
def test_a_malformed_save_is_refused(env_file, monkeypatch, clean_env, payload):
    monkeypatch.setattr(api_keys, "env_path", lambda: env_file)
    handler, responses = _page(
        {"X-Jarvis-Token": "testtoken", "Origin": "http://127.0.0.1:8765"}
    )
    handler._handle_keys_update(payload)
    assert responses[0][0] == HTTPStatus.BAD_REQUEST
    assert responses[0][1]["ok"] is False


def test_both_verbs_route_to_the_keys_handlers():
    """Calling the handlers directly proves they work, not that they are reachable."""
    get_source = inspect.getsource(BrowserRequestHandler.do_GET)
    post_source = inspect.getsource(BrowserRequestHandler.do_POST)

    assert '"/api/keys"' in get_source
    assert "self._handle_keys()" in get_source
    # POST is the one that writes, so its route has to sit inside the allow-list
    # that runs the origin check - not after it, where an unknown path falls
    # through to the shutdown handler.
    assert post_source.index("if path not in {") < post_source.index('            "/api/keys",')
    assert post_source.index('            "/api/keys",') < post_source.index(
        "self._handle_keys_update(payload)"
    )


# --------------------------------------------------------------------------- #
#  The tab
# --------------------------------------------------------------------------- #


def test_the_keys_tab_exists_and_owns_a_panel():
    assert 'id="tabBtnKeys"' in INDEX_HTML
    assert 'aria-controls="tabKeys"' in INDEX_HTML
    assert 'id="tabKeys"' in INDEX_HTML
    assert 'data-tab="keys"' in INDEX_HTML
    # The panel is hidden until it is selected, like every other tab.
    panel = INDEX_HTML[INDEX_HTML.index('id="tabKeys"') :]
    assert " hidden>" in panel[: panel.index("</section>")]


def test_the_tab_strip_indicator_matches_the_number_of_tabs():
    """The underline is one tab wide and moved in whole tabs; a seventh tab that
    forgot this would leave the indicator pointing at the wrong label."""
    import re

    tabs = INDEX_HTML.count('class="panel-tab"')
    match = re.search(r"\.tab-underline\s*\{[^}]*width:\s*calc\(100%\s*/\s*(\d+)\)", STYLES_CSS)
    assert match, "the tab underline no longer divides its width by the tab count"
    assert int(match.group(1)) == tabs == 6


def test_the_tab_only_prints_a_raw_value_for_a_non_secret():
    """The page is given masks, and it must not print anything else for a key."""
    assert '"/api/keys"' in APP_JS
    assert "entry.masked" in APP_JS
    guard = APP_JS.index("if (!entry.secret)")
    use = APP_JS.index("entry.value")
    assert guard < use, "a raw value is only reachable behind the secret check"
    assert APP_JS.count("entry.value") == 1


def test_selecting_the_tab_loads_the_credentials():
    start = APP_JS.index("function selectTab(")
    body = APP_JS[start : APP_JS.index("\n  }", start)]
    assert 'if (name === "keys") fetchKeys();' in body
