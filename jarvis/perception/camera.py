"""The webcam, as something the agent can call.

``live_vision`` already owns how a frame is grabbed - including the Windows
DirectShow path and the "camera offline or blocked by privacy settings" case - so
this module owns only what a *tool* needs on top: where the picture is kept, and a
name that says when it was taken.

**Nothing here calls a model.** The frame is attached to the agent's own next
vision turn (``ActionResult.image_path``), so the model that reasons about the
picture is the one that asked to look. The ``see`` action takes the other route -
a second brain describing the frame back as prose - which costs another round trip
and throws away everything prose cannot carry.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional, Tuple

from ..utils import logging as log

#: JPEG, at a quality that keeps a face and a document legible without shipping a
#: multi-megabyte PNG to a model on every look.
_QUALITY = 85

#: Webcam indexes the action will accept, matching what ``capture_webcam`` clamps.
_MAX_INDEX = 9


def camera_dir() -> Path:
    """Where frames are kept, beside the archived screenshots."""
    from ..utils.paths import state_root

    return state_root() / "dataset" / "data" / "camera"


def _opencv_version() -> str:
    try:
        import cv2  # type: ignore

        return str(getattr(cv2, "__version__", "?"))
    except Exception:
        return ""


def status() -> str:
    """One honest line on whether looking through the camera can work at all."""
    version = _opencv_version()
    if not version:
        return ("no camera support: OpenCV is not installed, so the camera cannot "
                "look at anything (pip install opencv-python)")
    return (f"camera support present (OpenCV {version}); pictures are saved under "
            f"{camera_dir()}")


def available() -> bool:
    """Whether a frame can be captured at all, without taking one.

    Cheap on purpose: it answers "is the camera machinery here", not "is there a
    camera" - the only way to know that is to take a picture, and taking one is
    what the caller wanted to decide about.
    """
    return bool(_opencv_version())


def snapshot(camera_index: int = 0) -> Tuple[Optional[Path], str, Tuple[int, int]]:
    """Take one frame and save it. Returns ``(path, message, (width, height))``.

    A ``None`` path is a camera that did not answer, and the message says so
    rather than pretending: an agent told "looked at the webcam" when nothing was
    captured will describe a room it never saw.
    """
    from . import get_live_vision

    try:
        index = max(0, min(_MAX_INDEX, int(camera_index)))
    except (TypeError, ValueError):
        index = 0

    started = time.perf_counter()
    try:
        frame = get_live_vision().capture_webcam(camera_index=index)
    except Exception as exc:                      # a driver fault is not a crash
        log.warn(f"camera {index} raised while capturing ({exc}).")
        frame = None
    if frame is None:
        return None, (
            f"no picture: camera {index} did not answer. It may be switched off, "
            "already in use by another app, or blocked under Windows Settings > "
            "Privacy & security > Camera."), (0, 0)

    path = camera_dir() / f"camera-{time.strftime('%Y%m%d-%H%M%S')}-{index}.jpg"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.convert("RGB").save(path, "JPEG", quality=_QUALITY)
    except Exception as exc:
        log.warn(f"camera frame could not be saved ({exc}).")
        return None, f"the picture could not be saved ({exc})", (0, 0)

    size = (frame.width, frame.height)
    took = time.perf_counter() - started
    log.ok(f"camera {index}: one frame, {size[0]}x{size[1]}, in {took:.1f}s.")
    return path, (
        f"camera {index} picture taken ({size[0]}x{size[1]}, {took:.1f}s) - it is "
        f"attached to your next turn; look at it there and say what you actually "
        f"see. Saved to {path}"), size
