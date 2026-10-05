"""Named click coordinates: the memory that stops Jarvis re-finding a button.

Two halves are covered here, because the feature is only real if both work:

  * ``jarvis/memory/coordinates.py`` - the store itself (naming, kinds,
    searching, resolution scaling, tolerance of a corrupt file, eviction);
  * the seams that consume it - the pointer actions' ``coord`` argument, the
    ``coordinates`` action, the remote_task hint handed to a phone, and the
    prompt block that surfaces the entries a task is likely to need.
"""

from __future__ import annotations

import json

import pytest

from jarvis.config import Config
from jarvis.memory import coordinates as coords
from jarvis.perception.elements import Element, Observation
from jarvis.tools import registry
from jarvis.tools.schema import ACTIONS_BY_NAME, to_json_schema
from jarvis.utils import paths


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """A store in its own file, used by the registry path too.

    Both halves matter: the registry resolves its store through
    ``default_store_path``, so patching that is what keeps these tests off the
    shared state directory - and keeps one test's saved names out of another
    test's searches.
    """
    path = tmp_path / "jarvis_coordinates.json"
    monkeypatch.setattr(coords, "default_store_path", lambda: path)
    return coords.CoordinateStore(path)


def _obs(screen: tuple[int, int] = (1920, 1080), elements: bool = True) -> Observation:
    """A desktop observation whose one element is nowhere near the fixtures' points."""
    return Observation(
        elements=([Element(0, "Button", "Compose", (1500, 900, 1560, 940), (1530, 920))]
                  if elements else []),
        screen_size=screen,
        active_window="WhatsApp",
    )


# --------------------------------------------------------------------------- #
# the store
# --------------------------------------------------------------------------- #

def test_kinds_collapse_to_the_two_screens():
    assert coords.normalize_kind("phone") == "mobile"
    assert coords.normalize_kind("Android") == "mobile"
    assert coords.normalize_kind("this pc") == "pc"
    assert coords.normalize_kind("desktop") == "pc"
    # An unrecognised word must land on the caller's screen, never raise.
    assert coords.normalize_kind("chromebook", default="mobile") == "mobile"
    assert coords.normalize_kind("", default="mobile") == "mobile"


def test_names_are_slugged_and_screens_parsed():
    assert coords.slugify("WhatsApp Send Button") == "whatsapp-send-button"
    assert coords.slugify("  Send/Button!! ") == "send-button"
    assert coords.parse_screen("1080x2400") == (1080, 2400)
    assert coords.parse_screen([1920, 1080]) == (1920, 1080)
    assert coords.parse_screen("") == (0, 0)


def test_saved_target_is_found_by_the_words_of_the_request(store):
    store.save("whatsapp-send-button", kind="pc", x=1185, y=842,
               screen_w=1920, screen_h=1080, app="WhatsApp",
               note="green send arrow in the composer")

    for query in ("whatsapp send", "send button", "whatsapp-send-button", "composer send"):
        assert [e.name for e in store.search(query)] == ["whatsapp-send-button"], query
    assert store.search("spotify play") == []
    assert store.get("WhatsApp Send Button").x == 1185     # slug lookup, any case


def test_the_two_screens_never_cross(store):
    store.save("send", kind="pc", x=1185, y=842)
    store.save("send", kind="mobile", x=540, y=1180, screen_w=1080, screen_h=2400)

    assert store.get("send", kind="pc").y == 842
    assert store.get("send", kind="mobile").y == 1180
    assert [e.kind for e in store.search("send", kind="mobile")] == ["mobile"]
    assert len(store.load()) == 2


def test_resaving_moves_the_entry_and_keeps_its_history(store):
    first = store.save("send", kind="pc", x=10, y=10, screen_w=1920, screen_h=1080)
    store.record_use("send", kind="pc", ok=True)
    moved = store.save("send", kind="pc", x=99, y=77, screen_w=1920, screen_h=1080)

    assert (moved.x, moved.y) == (99, 77)
    assert moved.uses == 1 and moved.successes == 1      # not a fresh entry
    assert moved.created == first.created
    assert len(store.load()) == 1


def test_resolution_change_scales_instead_of_missing(store):
    store.save("send", kind="pc", x=1200, y=600, screen_w=1920, screen_h=1080)

    smaller = store.resolve("send", kind="pc", screen_w=1280, screen_h=720)
    assert (smaller.x, smaller.y) == (800, 400)
    # Same screen -> the stored pixels, untouched.
    same = store.resolve("send", kind="pc", screen_w=1920, screen_h=1080)
    assert (same.x, same.y) == (1200, 600)
    # No screen recorded at all -> the pixels are all we have.
    unknown = store.resolve("send", kind="pc")
    assert (unknown.x, unknown.y) == (1200, 600)


def test_forget_matches_across_both_screens(store):
    store.save("whatsapp-send-button", kind="pc", x=1, y=1)
    store.save("whatsapp-send-button", kind="mobile", x=2, y=2)
    store.save("spotify-play-button", kind="pc", x=3, y=3)

    removed = store.forget("whatsapp send")
    assert sorted(e.kind for e in removed) == ["mobile", "pc"]
    assert [e.name for e in store.load()] == ["spotify-play-button"]


def test_a_corrupt_file_starts_the_table_over_without_raising(tmp_path, monkeypatch):
    path = tmp_path / "jarvis_coordinates.json"
    monkeypatch.setattr(coords, "default_store_path", lambda: path)
    path.write_text("{not json at all", encoding="utf-8")

    assert coords.CoordinateStore(path).load() == []
    coords.CoordinateStore(path).save("send", kind="pc", x=5, y=6)
    assert json.loads(path.read_text(encoding="utf-8"))["entries"][0]["name"] == "send"


def test_the_table_is_capped_keeping_what_is_actually_used(store, monkeypatch):
    monkeypatch.setattr(coords, "MAX_ENTRIES", 3)
    store.save("target-0", kind="pc", x=0, y=0)
    for _ in range(4):
        store.record_use("target-0", kind="pc", ok=True)
    for i in range(1, 5):
        store.save(f"target-{i}", kind="pc", x=i, y=i)

    kept = {e.name for e in store.load()}
    assert len(kept) == 3
    assert "target-0" in kept          # the one that earned its place


def test_default_path_follows_the_state_root():
    assert coords.default_store_path().name == coords.STORE_FILENAME
    assert coords.default_store_path().parent == paths.state_root()


# --------------------------------------------------------------------------- #
# the click seam
# --------------------------------------------------------------------------- #

def test_click_by_saved_name_needs_no_screenshot(store, monkeypatch):
    cfg = Config()
    store.save("spotify-play-button", kind="pc", x=1800, y=1000,
               screen_w=1920, screen_h=1080, app="Spotify")

    clicked: list[tuple] = []
    monkeypatch.setattr(registry.mouse, "click", lambda *a, **k: clicked.append(a) or "clicked")

    result = registry.execute("click", {"coord": "spotify-play-button"}, _obs(), cfg)

    assert result.ok, result.message
    assert clicked == [(1800, 1000)]           # not snapped to the one element
    assert '"spotify-play-button"' in result.message
    # The use is recorded, so the prompt can rank it next time.
    assert store.get("spotify-play-button").uses == 1


def test_a_half_remembered_name_still_clicks(store, monkeypatch):
    cfg = Config()
    store.save("spotify-play-button", kind="pc", x=1800, y=1000,
               screen_w=1920, screen_h=1080, app="Spotify")
    clicked: list[tuple] = []
    monkeypatch.setattr(registry.mouse, "click", lambda *a, **k: clicked.append(a) or "clicked")

    assert registry.execute("click", {"coord": "spotify play"}, _obs(), cfg).ok
    assert clicked == [(1800, 1000)]


def test_an_ambiguous_name_is_not_guessed_between(store):
    store.save("whatsapp-send-button", kind="pc", x=1, y=1, app="WhatsApp")
    store.save("telegram-send-button", kind="pc", x=2, y=2, app="Telegram")

    point, err = registry._resolve_point({"coord": "send button"}, _obs())

    assert point is None
    assert "whatsapp-send-button" in err and "telegram-send-button" in err


def test_element_id_outranks_a_saved_name(store, monkeypatch):
    cfg = Config()
    store.save("compose", kind="pc", x=10, y=10, screen_w=1920, screen_h=1080)
    clicked: list[tuple] = []
    monkeypatch.setattr(registry.mouse, "click", lambda *a, **k: clicked.append(a) or "clicked")

    registry.execute("click", {"coord": "compose", "element": 0}, _obs(), cfg)
    assert clicked == [(1530, 920)]            # ground truth for THIS turn wins


def test_mobile_only_name_is_refused_with_the_phone_fix(store):
    store.save("phone-whatsapp-send", kind="mobile", x=540, y=1180,
               screen_w=1080, screen_h=2400)

    point, err = registry._resolve_point({"coord": "phone-whatsapp-send"}, _obs())

    assert point is None
    assert "MOBILE" in err
    assert "remote_task" in err               # tells the model where it belongs


def test_unknown_name_suggests_the_closest_saved_names(store):
    store.save("whatsapp-send-button", kind="pc", x=1, y=1, app="WhatsApp")

    # Close enough to be worth suggesting, too far to click on the model's behalf.
    point, err = registry._resolve_point({"coord": "send arrow"}, _obs())

    assert point is None
    assert "whatsapp-send-button" in err
    assert "coordinates" in err               # points at the search action


def test_pointer_echoes_the_name_not_the_pixels(store):
    store.save("send", kind="pc", x=1185, y=842, screen_w=1920, screen_h=1080)
    assert registry._target_desc({"coord": "send"}, _obs()) == 'saved "send"'


def test_schema_offers_coord_on_the_pointer_actions():
    schema = {entry["name"]: entry for entry in to_json_schema()}
    for action in ("click", "double_click", "triple_click", "right_click"):
        assert "coord" in schema[action]["parameters"]["properties"], action
        # Optional: a target can still be an element id or raw pixels.
        assert "coord" not in schema[action]["parameters"]["required"], action
    assert "coordinates" in ACTIONS_BY_NAME


# --------------------------------------------------------------------------- #
# the action
# --------------------------------------------------------------------------- #

def test_coordinates_action_saves_finds_lists_and_forgets(store):
    cfg = Config()
    obs = _obs()

    saved = registry.execute("coordinates", {
        "action": "save", "name": "WhatsApp Send", "kind": "pc",
        "x": 1185, "y": 842, "screen": "1920x1080", "app": "WhatsApp",
    }, obs, cfg)
    assert saved.ok and "whatsapp-send" in saved.message
    assert '"coord"' in saved.message           # teaches the click form
    # The slug is canonical: it is what the agent must type back.
    assert store.list_all()[0].name == "whatsapp-send"

    found = registry.execute("coordinates", {"action": "find", "query": "whatsapp send"},
                             obs, cfg)
    assert found.ok and "(1185,842)" in found.message

    listed = registry.execute("coordinates", {"action": "list"}, obs, cfg)
    assert listed.ok and "whatsapp-send" in listed.message

    gone = registry.execute("coordinates", {"action": "forget", "name": "whatsapp send"},
                            obs, cfg)
    assert gone.ok and store.list_all() == []


def test_coordinates_action_never_costs_a_screenshot(store):
    cfg = Config()
    obs = _obs()
    for args in ({"action": "save", "name": "send", "x": 5, "y": 6},
                 {"action": "find", "query": "send"},
                 {"action": "list"},
                 {"action": "forget", "name": "send"},
                 {"action": "nonsense"}):
        result = registry.execute("coordinates", args, obs, cfg)
        assert result.needs_observe is False, args


def test_coordinates_action_explains_what_it_was_missing(store):
    cfg = Config()
    no_name = registry.execute("coordinates", {"action": "save", "x": 5, "y": 6}, _obs(), cfg)
    assert not no_name.ok and "name" in no_name.message

    no_xy = registry.execute("coordinates", {"action": "save", "name": "send"}, _obs(), cfg)
    assert not no_xy.ok and "x and y" in no_xy.message

    empty = registry.execute("coordinates", {"action": "find", "query": "nothing here"},
                             _obs(), cfg)
    assert empty.ok and "No saved coordinates yet" in empty.message


# --------------------------------------------------------------------------- #
# the phone
# --------------------------------------------------------------------------- #

def test_mobile_coordinates_ride_along_with_a_remote_task(store, monkeypatch):
    cfg = Config()
    store.save("phone-whatsapp-send", kind="mobile", x=540, y=1180,
               screen_w=1080, screen_h=2400, app="WhatsApp", note="send arrow")

    captured: dict[str, str] = {}

    def fake_send_task(cfg_, device, task, timeout=None):
        captured["task"] = task
        return True, "Mobile: done", None

    monkeypatch.setattr("jarvis.remote.send_task", fake_send_task)
    result = registry._h_remote_task(
        {"device": "Mobile", "task": "send a message to my friend on WhatsApp"},
        None, cfg)

    assert result.ok
    assert "tap 540 1180" in captured["task"]
    assert "phone-whatsapp-send" in captured["task"]


def test_a_task_with_no_saved_phone_coordinates_is_sent_unchanged(store, monkeypatch):
    cfg = Config()
    store.save("phone-whatsapp-send", kind="mobile", x=540, y=1180)

    captured: dict[str, str] = {}

    def fake_send_task(cfg_, device, task, timeout=None):
        captured["task"] = task
        return True, "Mobile: done", None

    monkeypatch.setattr("jarvis.remote.send_task", fake_send_task)
    registry._h_remote_task({"device": "Mobile", "task": "open the camera"}, None, cfg)

    assert captured["task"] == "open the camera"


def test_pc_coordinates_are_not_offered_to_the_phone(store):
    store.save("whatsapp-send-button", kind="pc", x=1185, y=842)
    assert registry._mobile_coord_hint("send a message on whatsapp") == ""


# --------------------------------------------------------------------------- #
# the prompt block
# --------------------------------------------------------------------------- #

def test_note_lists_only_what_the_task_is_about(store):
    store.save("whatsapp-send-button", kind="pc", x=1185, y=842, app="WhatsApp")
    store.save("spotify-play-button", kind="pc", x=50, y=60, app="Spotify")

    block = coords.note("send a message to my friend on whatsapp")

    assert "whatsapp-send-button" in block
    assert "spotify-play-button" not in block
    assert "COORDINATES YOU ALREADY KNOW" in block
    assert '"coord"' in block
    # Nothing relevant -> no block at all, like every other prompt note.
    assert coords.note("what is the weather") == ""
    assert coords.note("") == ""


def test_note_also_matches_the_active_window(store):
    store.save("photoshop-save-icon", kind="pc", x=1900, y=40, app="Photoshop")
    assert "photoshop-save-icon" in coords.note("", "Adobe Photoshop")
