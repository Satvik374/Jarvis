"""The camera tool: taking a picture, and handing it to the model that asked.

**No camera is opened here.** ``live_vision`` is stubbed with a synthetic frame, so
what is under test is this tool's own decisions: that a frame becomes an *attached
image* rather than a paragraph about one, that a camera which does not answer says
so instead of letting the model invent a room, and that asking to look at the
camera reaches the control loop at all - the chat path has no tools, and used to
answer this by explaining that it cannot see.
"""

from __future__ import annotations

import pathlib

import pytest
from PIL import Image, ImageDraw

from jarvis.perception import camera
from jarvis.tools import registry, schema


class FakeEngine:
    """Stands in for ``LiveVisionEngine``: a frame, or a camera that says nothing."""

    def __init__(self, frame=None, raises=None):
        self.frame = frame
        self.raises = raises
        self.asked: list[int] = []

    def capture_webcam(self, camera_index: int = 0, warmup_frames: int = 2):
        self.asked.append(camera_index)
        if self.raises is not None:
            raise self.raises
        return self.frame


def _scene() -> Image.Image:
    """A frame with structure in it, so "is there a picture" is a real question."""
    img = Image.new("RGB", (320, 240), (30, 40, 60))
    draw = ImageDraw.Draw(img)
    draw.rectangle((40, 40, 200, 180), fill=(200, 120, 40))
    draw.ellipse((220, 60, 300, 140), fill=(240, 240, 220))
    return img


@pytest.fixture
def camera_state(monkeypatch, tmp_path):
    """Frames go to a temp state root, and nothing reaches a real webcam."""
    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path))
    engine = FakeEngine(frame=_scene())
    monkeypatch.setattr("jarvis.perception.get_live_vision", lambda: engine)
    return engine


def test_the_camera_is_declared_and_bound():
    """The schema and the handler are two halves of one action, and the tool's own
    guidance is load-bearing: the model only knows the frame is coming because the
    declaration says so."""
    assert "camera" in schema.ACTIONS_BY_NAME
    assert registry.handler_for("camera") is not None
    declared = schema.ACTIONS_BY_NAME["camera"]
    assert "next turn" in declared.summary, \
        "the model has to be told the picture arrives on the next turn"
    assert {p.name for p in declared.params} == {"op", "camera"}


def test_a_look_returns_the_frame_as_an_attached_image(camera_state):
    """The whole point: one tool call, and the picture - not prose about it."""
    result = registry.execute("camera", {"op": "look"}, None, None)

    assert result.ok
    assert result.image_path and pathlib.Path(result.image_path).exists()
    assert "next turn" in result.message
    saved = Image.open(result.image_path)
    assert saved.size == (320, 240), "the frame that was taken is the frame that lands"
    assert pathlib.Path(result.image_path).parent == camera.camera_dir()
    assert camera_state.asked == [0]


def test_looking_does_not_walk_the_screen(camera_state):
    """A webcam frame does not change the desktop, and the observation it would
    trigger costs more than the capture: the walk is skipped on purpose."""
    assert registry.execute("camera", {"op": "look"}, None, None).needs_observe is False


def test_a_camera_that_does_not_answer_says_so(camera_state, monkeypatch):
    """``capture_webcam`` returns None when the camera is off, busy or blocked. The
    honest reply names those reasons - an agent told "looked at the webcam" when
    nothing was captured will describe a room it never saw."""
    monkeypatch.setattr("jarvis.perception.get_live_vision",
                        lambda: FakeEngine(frame=None))

    result = registry.execute("camera", {"op": "look"}, None, None)

    assert not result.ok
    assert result.image_path is None
    assert "did not answer" in result.message
    assert "Privacy" in result.message, "say where to look, not just that it failed"


def test_a_camera_that_throws_is_a_failure_not_a_crash(camera_state, monkeypatch):
    monkeypatch.setattr("jarvis.perception.get_live_vision",
                        lambda: FakeEngine(raises=RuntimeError("driver said no")))

    result = registry.execute("camera", {"op": "look"}, None, None)

    assert not result.ok and result.image_path is None


def test_the_camera_index_is_bounded(camera_state):
    """A camera number comes from a model, so it is clamped like every other
    argument - an index of 99 must not reach the driver."""
    registry.execute("camera", {"op": "look", "camera": 99}, None, None)
    assert camera_state.asked == [9]

    registry.execute("camera", {"op": "look", "camera": "nonsense"}, None, None)
    assert camera_state.asked[-1] == 0


def test_status_says_when_there_is_no_camera_support(monkeypatch):
    monkeypatch.setattr(camera, "_opencv_version", lambda: "")

    line = registry.execute("camera", {"op": "status"}, None, None).message

    assert "OpenCV is not installed" in line and "opencv-python" in line


def test_an_unknown_op_is_refused(camera_state):
    result = registry.execute("camera", {"op": "stare"}, None, None)

    assert not result.ok and "look" in result.message
    assert camera_state.asked == [], "an unknown op must not open the camera"


def test_the_camera_stays_out_of_the_published_voice_set():
    """The voice agent's direct tool list is published and pinned by a count, so an
    action that joined it silently would leave the hosted agent advertising a list
    it no longer has. ``camera`` sits here for the same reason ``see`` does: it is a
    picture for a model to look at, and the voice path is given resolving screen
    tools instead. Making it voice-callable is a deliberate act - remove the row,
    bump the counts, re-publish."""
    from jarvis.live import direct_tools

    assert "camera" not in direct_tools.names()
    assert direct_tools.EXCLUDED.get("camera") == "screen"
    # The task loop is the caller that does get it.
    assert "camera" in schema.ACTIONS_BY_NAME


def _router():
    """The console's own router, without building a brain or a desktop for it."""
    from jarvis.agent.loop import Agent

    agent = object.__new__(Agent)
    for name in dir(Agent):
        if name.startswith(("_TASK_", "_UI_", "_KEY_")):
            setattr(agent, name, getattr(Agent, name))
    return agent._looks_like_task


def test_asking_to_look_at_the_camera_is_a_task_not_chat():
    """"Look through the camera" names no UI target and starts with a word too
    ambiguous for the verb list, so it used to reach the chat path - which has no
    tools and answered that Jarvis cannot see. Mentioning the camera is the
    signal."""
    router = _router()

    for phrase in ("Look through the camera and tell me what you see",
                   "Can you see me on the webcam?",
                   "take a photo of me",
                   "what do I look like right now"):
        assert router(phrase), phrase


def test_camera_words_do_not_swallow_ordinary_conversation():
    """The new rule has to be narrow: "look" alone is exactly the ambiguity the
    router was built to avoid."""
    router = _router()

    for phrase in ("look, I need help with my essay plan",
                   "how are you doing today",
                   "that photo you showed me yesterday was funny"):
        assert not router(phrase), phrase
