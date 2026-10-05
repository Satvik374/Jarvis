"""The app index, and the fast ``open_app`` it makes possible.

``open_app`` used to hand a friendly name to ``cmd /c start`` and sleep a full
second - a guess that resolves only PATH and App Paths, followed by a wait that
was spent asleep rather than looking. These tests pin the replacement: the name
is resolved against the same data Windows search shows (App Paths + Start Menu
shortcuts), the launch is confirmed by the *window list* rather than assumed, and
nothing here ever starts a real app.

The index is read from a fake Start Menu directory, so the assertions are about
the matching rules (exact, prefix, fuzzy, alias, refusal) instead of about what
happens to be installed on the machine running the suite.
"""

from __future__ import annotations

import os

import pytest

from jarvis.tools import app_index, apps


@pytest.fixture(autouse=True)
def _clean_index():
    app_index.invalidate()
    yield
    app_index.invalidate()


@pytest.fixture
def start_menu(monkeypatch, tmp_path):
    """A stand-in Start Menu: four shortcuts, one decoy, one nested."""
    for name in ("CapCut.lnk", "Notepad++.lnk", "Visual Studio Code.lnk",
                 "Calculator.lnk", "Uninstall CapCut.lnk"):
        (tmp_path / name).write_text("", encoding="utf-8")
    nested = tmp_path / "Adobe"
    nested.mkdir()
    (nested / "Photoshop 2024.lnk").write_text("", encoding="utf-8")

    monkeypatch.setattr(app_index, "_start_menu_dirs", lambda: [str(tmp_path)])
    monkeypatch.setattr(app_index, "_registry_entries", lambda: [])
    return tmp_path


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #

def test_the_index_finds_a_shortcut_by_name(start_menu):
    match = app_index.resolve("capcut")

    assert match is not None
    assert match.name == "CapCut"
    assert match.target.endswith("CapCut.lnk")
    assert match.rail == "start-menu"
    assert match.score == 1.0


def test_matching_ignores_case_and_filler_words(start_menu):
    for spoken in ("CapCut", "the capcut app", "open capcut please"):
        match = app_index.resolve(spoken)
        assert match is not None and match.name == "CapCut", spoken


def test_a_partial_name_resolves_to_the_longest_clean_prefix(start_menu):
    match = app_index.resolve("photoshop")

    assert match is not None
    assert match.name == "Photoshop 2024"
    assert match.score >= app_index.MIN_SCORE


def test_a_spoken_alias_maps_onto_windows_own_label(start_menu):
    """Nobody says "Visual Studio Code"; they say "vs code" or "code"."""
    for spoken in ("code", "vs code", "vscode"):
        match = app_index.resolve(spoken)
        assert match is not None and match.name == "Visual Studio Code", spoken
        # The alias maps the words; the *score* is the label's own, so a weak
        # match cannot be blessed into a confident one by the alias table.
        assert match.score >= app_index.MIN_SCORE


def test_a_short_word_inside_a_long_label_is_not_a_match(monkeypatch, tmp_path):
    """Measured failure: "whatsapp" matched "What is new in the latest version"
    on the single word "what". One short word in a long label is a coincidence."""
    (tmp_path / "What is new in the latest version.lnk").write_text(
        "", encoding="utf-8")
    monkeypatch.setattr(app_index, "_start_menu_dirs", lambda: [str(tmp_path)])
    monkeypatch.setattr(app_index, "_registry_entries", lambda: [])
    monkeypatch.setattr(app_index, "_start_apps_entries", lambda: [])
    app_index.invalidate()

    assert app_index.resolve("whatsapp") is None
    # The words it really contains still resolve.
    assert app_index.resolve("what is new in the latest version") is not None


def test_a_real_word_overlap_still_matches(monkeypatch, tmp_path):
    """The share test must not break a genuine multi-word overlap."""
    (tmp_path / "Visual Studio Code.lnk").write_text("", encoding="utf-8")
    monkeypatch.setattr(app_index, "_start_menu_dirs", lambda: [str(tmp_path)])
    monkeypatch.setattr(app_index, "_registry_entries", lambda: [])
    app_index.invalidate()

    match = app_index.resolve("studio code")

    assert match is not None and match.name == "Visual Studio Code"


def test_a_long_unrelated_shortcut_name_is_not_fuzzy_matched(monkeypatch, tmp_path,
                                                            start_apps):
    """Measured false positive: "instagram" matched a Start Menu shortcut called
    "What is new in the latest version" because the request *starts with* the
    word "in". A two-letter overlap is not evidence of anything."""
    (tmp_path / "What is new in the latest version.lnk").write_text(
        "", encoding="utf-8")
    monkeypatch.setattr(app_index, "_start_menu_dirs", lambda: [str(tmp_path)])
    monkeypatch.setattr(app_index, "_registry_entries", lambda: [])
    app_index.invalidate()

    match = app_index.resolve("instagram")

    assert match is not None, "the shell's roster knows this app"
    assert match.rail == "start-apps"
    assert app_index.resolve("what is new in the latest version") is not None


def test_a_name_that_is_not_there_resolves_to_nothing(start_menu):
    assert app_index.resolve("bogus-app-xyz") is None
    assert app_index.resolve("") is None
    assert app_index.resolve("open the app") is None


def test_a_verb_the_index_cannot_perform_is_refused_not_fuzzed(start_menu):
    """'uninstall capcut' must never resolve to the CapCut shortcut."""
    assert app_index.resolve("uninstall capcut") is None
    assert app_index.resolve("remove notepad++") is None


def test_uninstallers_and_readmes_are_not_candidates(start_menu):
    """A Start Menu is full of entries nobody means by an app name."""
    stems = {entry.stem for entry in app_index.entries()}

    assert "Uninstall CapCut" not in stems
    assert "notepad++" in {stem.lower() for stem in stems}


def test_scores_rank_exact_above_prefix_above_inside_above_fuzzy():
    exact = app_index._score("capcut", "capcut")
    prefix = app_index._score("capcut", "capcut studio")
    inside = app_index._score("capcut", "my capcut editor")
    # A near-miss sharing no prefix and no word is the fuzzy tier.
    fuzzy = app_index._score("capkut", "capcut")
    # A prefix decays with the extra label it carries: the shortest tail is the
    # most specific answer (an app actually called "CapCut" over "CapCutting").
    short_tail = app_index._score("capcut", "capcutting")
    long_tail = app_index._score("capcut", "capcut studio")

    assert exact == 1.0
    assert exact > prefix > inside
    assert short_tail > long_tail
    assert inside >= app_index.MIN_SCORE > fuzzy, \
        "a fuzzy near-miss must not clear the bar on its own"


def test_the_index_is_built_once_and_reused(monkeypatch, start_menu):
    builds = []
    real = app_index._start_menu_entries

    def counted():
        builds.append(1)
        return real()

    monkeypatch.setattr(app_index, "_start_menu_entries", counted)

    assert app_index.resolve("capcut") is not None
    assert app_index.resolve("notepad++") is not None
    assert len(builds) == 1, "a second lookup must not re-walk the Start Menu"


@pytest.fixture
def start_apps(monkeypatch):
    """The shell's roster, stubbed: a real one costs a PowerShell start."""
    calls: list[int] = []
    roster = [
        app_index.Entry("Calculator",
                        "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App",
                        "start-apps"),
        app_index.Entry("Instagram",
                        "Facebook.InstagramBeta_8wekyb3d8bbwe!App",
                        "start-apps"),
    ]

    def fake():
        calls.append(1)
        return list(roster)

    monkeypatch.setattr(app_index, "_start_apps_entries", fake)
    return calls


def test_a_store_app_is_found_by_the_shells_own_roster(start_menu, start_apps):
    """Store apps have no shortcut to walk, so names like this need the roster."""
    match = app_index.resolve("instagram")

    assert match is not None
    assert match.rail == "start-apps"
    assert match.target.endswith("!App")
    assert app_index.describe(match) == '"Instagram" via Windows Start apps'


def test_the_slow_roster_is_paid_for_only_on_a_miss(start_menu, start_apps):
    """A name the cheap rails answer must not start a PowerShell."""
    assert app_index.resolve("capcut") is not None
    assert start_apps == []

    assert app_index.resolve("instagram") is not None
    assert len(start_apps) == 1


def test_an_empty_roster_is_cached_rather_than_retried(monkeypatch, start_menu):
    """A machine without the cmdlet must not pay its cost on every lookup."""
    calls: list[int] = []

    def fake():
        calls.append(1)
        return []

    monkeypatch.setattr(app_index, "_start_apps_entries", fake)

    assert app_index.resolve("nothing-here-xyz") is None
    assert app_index.resolve("nothing-here-xyz-either") is None
    assert len(calls) == 1


def test_only_launchable_roster_targets_are_kept():
    """A CSIDL-relative spec cannot be started without the shell interpreting
    it, so it must not be offered as a match at all."""
    assert app_index._launchable(
        "{6D809377-6AF0-444B-8957-A3773F02200E}\\Audacity\\Audacity.exe") is False
    assert app_index._launchable("Microsoft.WindowsCalculator_8wekyb3d8bbwe!App")
    assert app_index._launchable("") is False


def test_the_match_explains_itself(start_menu):
    match = app_index.resolve("capcut")
    assert app_index.describe(match) == '"CapCut" via Start Menu'


# --------------------------------------------------------------------------- #
# open_app
# --------------------------------------------------------------------------- #

@pytest.fixture
def no_launch(monkeypatch):
    """Record any launch instead of performing it - a test must not open apps."""
    started: list = []
    monkeypatch.setattr(os, "startfile", started.append, raising=False)
    # Signature-compatible: the real call passes (argv, shell=False).
    monkeypatch.setattr(apps.subprocess, "Popen",
                        lambda argv, *a, **k: started.append(argv))
    monkeypatch.setattr(apps, "focus_window", lambda title: "no window matching")
    return started


def test_open_app_launches_the_indexed_shortcut_and_names_it(monkeypatch,
                                                             no_launch):
    match = app_index.Match("CapCut", r"C:\Start Menu\CapCut.lnk",
                            "start-menu", 1.0)
    monkeypatch.setattr(apps.app_index, "resolve", lambda name: match)
    monkeypatch.setattr(apps, "_window_evidence", lambda words: "CapCut")

    result = apps.open_app("capcut")

    assert result.startswith("launched 'capcut'")
    assert '"CapCut" via Start Menu' in result
    assert "window 'CapCut' is up" in result
    assert no_launch == [r"C:\Start Menu\CapCut.lnk"]


def test_open_app_does_not_claim_a_window_that_never_appeared(monkeypatch,
                                                              no_launch):
    """The launch is reported, the *evidence* is reported as absent."""
    match = app_index.Match("CapCut", r"C:\Start Menu\CapCut.lnk",
                            "start-menu", 1.0)
    monkeypatch.setattr(apps.app_index, "resolve", lambda name: match)
    monkeypatch.setattr(apps, "_window_evidence", lambda words: "")

    result = apps.open_app("capcut")

    assert "no matching window has appeared yet" in result


def test_open_app_opens_a_store_app_through_appsfolder(monkeypatch, no_launch):
    """No executable exists for a Store app: the shell opens it by app id."""
    match = app_index.Match("Calculator",
                            "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App",
                            "start-apps", 1.0)
    monkeypatch.setattr(apps.app_index, "resolve", lambda name: match)
    monkeypatch.setattr(apps, "_window_evidence", lambda words: "Calculator")

    result = apps.open_app("calculator")

    assert result.startswith("launched 'calculator'")
    assert '"Calculator" via Windows Start apps' in result
    assert no_launch[0][0] == "explorer.exe"
    assert no_launch[0][1].startswith("shell:AppsFolder\\")


def test_open_app_focuses_an_app_that_is_already_open(monkeypatch, no_launch):
    monkeypatch.setattr(apps.app_index, "resolve", lambda name: None)
    monkeypatch.setattr(apps, "focus_window", lambda title: "focused 'Notepad'")

    assert apps.open_app("notepad") == "focused existing 'notepad'"
    assert no_launch == [], "an app that is already up is not launched again"


def test_a_name_the_index_cannot_resolve_falls_back_to_the_shell(
        monkeypatch, no_launch):
    monkeypatch.setattr(apps.app_index, "resolve", lambda name: None)
    monkeypatch.setattr(apps, "_window_evidence", lambda words: "")

    result = apps.open_app("bogus-app-xyz")

    assert "launched 'bogus-app-xyz'" in result
    assert no_launch, "the classic cmd/c start path is still the last resort"
    assert no_launch[0][:3] == ["cmd", "/c", "start"]
    assert "no matching window has appeared yet" in result


def test_the_window_wait_returns_the_moment_a_window_exists(monkeypatch):
    """It used to sleep a flat second; now it polls and returns early."""
    import time

    titles = iter([["Reactor Core"], ["Reactor Core", "CapCut"],
                   ["Reactor Core", "CapCut"]])
    monkeypatch.setattr(apps, "list_windows", lambda: next(titles, []))
    monkeypatch.setitem(__import__("sys").modules, "pygetwindow", object())

    started = time.perf_counter()
    evidence = apps._window_evidence(("capcut",), timeout=1.5)
    elapsed = time.perf_counter() - started

    assert evidence == "CapCut"
    assert elapsed < 0.5, "the window was already up; the wait must be short"


def test_the_window_wait_gives_up_instead_of_hanging(monkeypatch):
    monkeypatch.setattr(apps, "list_windows", lambda: ["Reactor Core"])
    monkeypatch.setitem(__import__("sys").modules, "pygetwindow", object())

    assert apps._window_evidence(("capcut",), timeout=0.1) == ""


def test_no_pygetwindow_means_no_wait_at_all(monkeypatch):
    """A machine with no window list cannot confirm a launch - and must not
    stand there waiting for a window it can never see."""
    import time

    monkeypatch.setitem(__import__("sys").modules, "pygetwindow", None)

    started = time.perf_counter()
    assert apps._window_evidence(("capcut",), timeout=1.5) == ""
    assert time.perf_counter() - started < 0.1
