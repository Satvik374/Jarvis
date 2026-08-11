"""Camera-driven hand control for the desktop pointer.

The public ``set_enabled`` function starts or stops a single background
controller.  Heavy camera/vision dependencies are imported inside the worker
so importing Jarvis (and building its action schema) stays lightweight.
"""

from __future__ import annotations

import atexit
import math
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_ACTIVE_MARGIN = 0.08
_SMOOTHING = 0.35
_PINCH_DOWN_RATIO = 0.32
_PINCH_RELEASE_RATIO = 0.52
_MIN_CLICK_INTERVAL = 0.25
_SCROLL_JOIN_RATIO = 0.30
_SCROLL_RELEASE_RATIO = 0.48
_SCROLL_STEP_DISTANCE = 0.035
_MAX_SCROLL_STEPS = 4
_VOLUME_JOIN_RATIO = 0.42
_VOLUME_RELEASE_RATIO = 0.62
_VOLUME_STEP_DISTANCE = 0.035
_MAX_VOLUME_STEPS = 4
_VOLUME_RELEASE_FRAMES = 3
_FINGER_EXTENDED_RATIO = 1.18
_FINGER_FOLDED_RATIO = 1.12
_THUMB_OPEN_RATIO = 0.55
_INDEX_MIDDLE_OPEN_RATIO = 0.20
_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/1/hand_landmarker.task"
)
_MIN_MODEL_BYTES = 1_000_000
_MAX_MODEL_BYTES = 25 * 1024 * 1024


def _map_axis(value: float, size: int, margin: float = _ACTIVE_MARGIN) -> int:
    """Map a normalized camera coordinate through an inner active region."""
    usable = max(0.01, 1.0 - (2.0 * margin))
    normalized = min(1.0, max(0.0, (float(value) - margin) / usable))
    # Keep automatic moves away from pyautogui's corner fail-safe points.
    return min(max(round(normalized * max(0, size - 1)), 3), max(3, size - 4))


def _landmark_distance(a: Any, b: Any, frame_width: int,
                       frame_height: int) -> float:
    dx = (float(a.x) - float(b.x)) * frame_width
    dy = (float(a.y) - float(b.y)) * frame_height
    return math.hypot(dx, dy)


def _finger_gap_ratio(landmarks: Any, first: int, second: int,
                      frame_width: int, frame_height: int) -> float:
    """Fingertip gap normalized by palm width for scale independence."""
    index_mcp = landmarks[5]
    pinky_mcp = landmarks[17]
    palm_width = _landmark_distance(index_mcp, pinky_mcp,
                                    frame_width, frame_height)
    if palm_width < 1.0:
        return math.inf
    return (_landmark_distance(landmarks[first], landmarks[second],
                               frame_width, frame_height) / palm_width)


def _pinch_ratio(landmarks: Any, frame_width: int,
                 frame_height: int) -> float:
    """Thumb/index distance normalized by palm width for scale independence."""
    return _finger_gap_ratio(landmarks, 4, 8, frame_width, frame_height)


def _triple_pinch_ratio(landmarks: Any, frame_width: int,
                        frame_height: int) -> float:
    """The larger thumb-to-fingertip gap for a thumb/index/middle pinch."""
    return max(_finger_gap_ratio(landmarks, 4, 8, frame_width, frame_height),
               _finger_gap_ratio(landmarks, 4, 12, frame_width, frame_height))


def _finger_extension_ratio(landmarks: Any, tip: int, pip: int,
                            frame_width: int, frame_height: int) -> float:
    """Fingertip reach compared to the same finger's middle joint reach."""
    wrist = landmarks[0]
    joint_distance = _landmark_distance(
        wrist, landmarks[pip], frame_width, frame_height)
    if joint_distance < 1.0:
        return math.inf
    return (_landmark_distance(wrist, landmarks[tip],
                               frame_width, frame_height) / joint_distance)


def _three_finger_volume_pose(landmarks: Any, frame_width: int,
                              frame_height: int) -> bool:
    """Thumb/index/middle open, ring/pinky folded."""
    index_ratio = _finger_extension_ratio(
        landmarks, 8, 6, frame_width, frame_height)
    middle_ratio = _finger_extension_ratio(
        landmarks, 12, 10, frame_width, frame_height)
    ring_ratio = _finger_extension_ratio(
        landmarks, 16, 14, frame_width, frame_height)
    pinky_ratio = _finger_extension_ratio(
        landmarks, 20, 18, frame_width, frame_height)
    thumb_gap = _finger_gap_ratio(landmarks, 4, 5, frame_width, frame_height)
    index_middle_gap = _finger_gap_ratio(
        landmarks, 8, 12, frame_width, frame_height)

    return (
        math.isfinite(thumb_gap)
        and math.isfinite(index_middle_gap)
        and index_ratio >= _FINGER_EXTENDED_RATIO
        and middle_ratio >= _FINGER_EXTENDED_RATIO
        and ring_ratio <= _FINGER_FOLDED_RATIO
        and pinky_ratio <= _FINGER_FOLDED_RATIO
        and thumb_gap >= _THUMB_OPEN_RATIO
        and index_middle_gap >= _INDEX_MIDDLE_OPEN_RATIO
    )


def _volume_clutch_pose(landmarks: Any, frame_width: int, frame_height: int,
                        active: bool) -> bool:
    """Return whether the current hand shape should own volume control."""
    if _three_finger_volume_pose(landmarks, frame_width, frame_height):
        return True

    # Keep supporting the earlier fingertip clutch as a fallback, but the main
    # user-facing gesture is the open three-finger pose above.
    triple_ratio = _triple_pinch_ratio(landmarks, frame_width, frame_height)
    threshold = _VOLUME_RELEASE_RATIO if active else _VOLUME_JOIN_RATIO
    return math.isfinite(triple_ratio) and triple_ratio <= threshold


@dataclass
class _PinchLatch:
    """Emit once on pinch-down and re-arm only after a clear release."""

    armed: bool = False

    def reset(self) -> None:
        self.armed = False

    def update(self, ratio: float) -> bool:
        if not math.isfinite(ratio):
            self.reset()
            return False
        if ratio >= _PINCH_RELEASE_RATIO:
            self.armed = True
            return False
        if self.armed and ratio <= _PINCH_DOWN_RATIO:
            self.armed = False
            return True
        return False


@dataclass
class _ScrollGesture:
    """Turn a joined index/middle pair's vertical travel into scroll steps."""

    active: bool = False
    anchor_y: float | None = None

    def reset(self) -> None:
        self.active = False
        self.anchor_y = None

    def update(self, gap_ratio: float, midpoint_y: float) -> int:
        if not math.isfinite(gap_ratio) or not math.isfinite(midpoint_y):
            self.reset()
            return 0

        if self.active:
            if gap_ratio >= _SCROLL_RELEASE_RATIO:
                self.reset()
                return 0
        elif gap_ratio <= _SCROLL_JOIN_RATIO:
            self.active = True
            self.anchor_y = float(midpoint_y)
            return 0
        else:
            return 0

        if self.anchor_y is None:
            self.anchor_y = float(midpoint_y)
            return 0

        # Camera y grows downward. pyautogui uses positive steps for up, so
        # anchor-current already has the desired sign in both directions.
        distance = self.anchor_y - float(midpoint_y)
        steps = math.trunc(distance / _SCROLL_STEP_DISTANCE)
        steps = max(-_MAX_SCROLL_STEPS, min(_MAX_SCROLL_STEPS, steps))
        if steps:
            # Consume only the distance represented by the emitted steps. This
            # avoids jitter repeats while preserving larger continued motion.
            self.anchor_y -= steps * _SCROLL_STEP_DISTANCE
        return steps


@dataclass
class _VolumeGesture:
    """Turn horizontal travel of the volume clutch into volume keys."""

    active: bool = False
    anchor_x: float | None = None
    release_frames: int = 0

    def reset(self) -> None:
        self.active = False
        self.anchor_x = None
        self.release_frames = 0

    def update(self, volume_pose: bool, midpoint_x: float) -> int:
        if not math.isfinite(midpoint_x):
            self.reset()
            return 0

        if self.active:
            if not volume_pose:
                self.release_frames += 1
                if self.release_frames >= _VOLUME_RELEASE_FRAMES:
                    self.reset()
                return 0
            self.release_frames = 0
        elif volume_pose:
            self.active = True
            self.anchor_x = float(midpoint_x)
            self.release_frames = 0
            return 0
        else:
            return 0

        if self.anchor_x is None:
            self.anchor_x = float(midpoint_x)
            return 0

        # The feed is mirrored, so larger x means the user's hand moved right.
        # Positive pyautogui volume keys increase system volume, which matches
        # the physical-instrument convention requested by the user.
        distance = float(midpoint_x) - self.anchor_x
        steps = math.trunc(distance / _VOLUME_STEP_DISTANCE)
        steps = max(-_MAX_VOLUME_STEPS, min(_MAX_VOLUME_STEPS, steps))
        if steps:
            # Keep the unconsumed fraction to prevent jitter repeats while the
            # user holds a steady position.
            self.anchor_x += steps * _VOLUME_STEP_DISTANCE
        return steps


def _model_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".jarvis"
    return root / "Jarvis" / "models" / "hand_landmarker.task"


def _ensure_task_model() -> Path:
    """Return a cached official MediaPipe task model, downloading once."""
    path = _model_path()
    if path.is_file() and path.stat().st_size >= _MIN_MODEL_BYTES:
        return path

    import requests

    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".task.part")
    total = 0
    try:
        with requests.get(_MODEL_URL, stream=True, timeout=(10, 60)) as response:
            response.raise_for_status()
            announced = int(response.headers.get("content-length", 0) or 0)
            if announced > _MAX_MODEL_BYTES:
                raise RuntimeError("the hand model download is unexpectedly large")
            with partial.open("wb") as output:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > _MAX_MODEL_BYTES:
                        raise RuntimeError(
                            "the hand model download exceeded the size limit")
                    output.write(chunk)
        if total < _MIN_MODEL_BYTES:
            raise RuntimeError("the downloaded hand model is incomplete")
        partial.replace(path)
        return path
    except Exception as exc:
        try:
            partial.unlink(missing_ok=True)
        except Exception:
            pass
        raise RuntimeError(
            "could not download the MediaPipe hand model; check the internet "
            f"connection ({exc})"
        ) from exc


def _create_hand_tracker(mp: Any) -> tuple[Any, Any]:
    """Create a tracker and normalize legacy/Tasks results to landmark lists."""
    hands_api = getattr(getattr(mp, "solutions", None), "hands", None)
    if hands_api is not None:
        tracker = hands_api.Hands(
            static_image_mode=False,
            max_num_hands=1,
            model_complexity=0,
            min_detection_confidence=0.65,
            min_tracking_confidence=0.60,
        )

        def detect_legacy(rgb: Any) -> list[Any]:
            result = tracker.process(rgb)
            return [item.landmark for item in
                    (getattr(result, "multi_hand_landmarks", None) or [])]

        return tracker, detect_legacy

    tasks = getattr(mp, "tasks", None)
    vision = getattr(tasks, "vision", None)
    if tasks is None or vision is None:
        raise RuntimeError("the installed MediaPipe has no hand tracking API")

    options = vision.HandLandmarkerOptions(
        base_options=tasks.BaseOptions(model_asset_path=str(_ensure_task_model())),
        running_mode=vision.RunningMode.VIDEO,
        num_hands=1,
        min_hand_detection_confidence=0.65,
        min_hand_presence_confidence=0.60,
        min_tracking_confidence=0.60,
    )
    tracker = vision.HandLandmarker.create_from_options(options)
    last_timestamp = [0]

    def detect_tasks(rgb: Any) -> list[Any]:
        timestamp = max(last_timestamp[0] + 1,
                        int(time.monotonic() * 1000))
        last_timestamp[0] = timestamp
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = tracker.detect_for_video(image, timestamp)
        return list(result.hand_landmarks)

    return tracker, detect_tasks


class HandMouseController:
    """Own the camera and hand-tracking worker for one Jarvis process."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop_event: threading.Event | None = None
        self._capture: Any = None
        self._running = False
        self._camera_index = 0
        self._last_error = ""

    def is_enabled(self) -> bool:
        with self._lock:
            return bool(self._running and self._thread
                        and self._thread.is_alive())

    def start(self, camera_index: int = 0) -> tuple[bool, str]:
        camera_index = max(0, min(9, int(camera_index)))
        with self._lock:
            if self._thread and self._thread.is_alive():
                return True, ("mouse_control is already on "
                              f"(camera {self._camera_index})")
            ready = threading.Event()
            stop_event = threading.Event()
            self._stop_event = stop_event
            self._last_error = ""
            self._running = False
            self._camera_index = camera_index
            thread = threading.Thread(
                target=self._run,
                args=(camera_index, stop_event, ready),
                name="jarvis-hand-mouse",
                daemon=True,
            )
            self._thread = thread
            thread.start()

        if not ready.wait(timeout=7.0):
            stop_event.set()
            return False, ("mouse_control could not start: the camera/hand "
                           "tracker did not become ready")

        with self._lock:
            if self._running and thread.is_alive():
                return True, ("mouse_control is on: move your open hand to "
                              "move the pointer; touch thumb and index finger "
                              "once to left-click; join index and middle "
                              "fingers and move them vertically to scroll; "
                              "hold thumb, index and middle open with ring "
                              "and pinky closed, then move right to raise or "
                              "left to lower volume")
            error = self._last_error or "the camera worker stopped"
        return False, f"mouse_control could not start: {error}"

    def stop(self) -> tuple[bool, str]:
        with self._lock:
            thread = self._thread
            stop_event = self._stop_event
            capture = self._capture
            was_running = bool(thread and thread.is_alive())
            if stop_event:
                stop_event.set()

        # release() also helps unblock a camera backend waiting in read().
        if capture is not None:
            try:
                capture.release()
            except Exception:
                pass
        if thread and thread is not threading.current_thread():
            thread.join(timeout=3.0)

        with self._lock:
            still_alive = bool(thread and thread.is_alive())
            if not still_alive:
                self._running = False
                self._thread = None
                self._stop_event = None
                self._capture = None
        if still_alive:
            return False, "mouse_control is still stopping; try again shortly"
        if was_running:
            return True, "mouse_control is off and the camera has been released"
        return True, "mouse_control is already off"

    def _run(self, camera_index: int, stop_event: threading.Event,
             ready: threading.Event) -> None:
        capture = None
        hands = None
        try:
            try:
                import cv2  # type: ignore
                import mediapipe as mp  # type: ignore
                import pyautogui  # type: ignore
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    f"missing dependency '{exc.name}'; run: "
                    "python -m pip install -r requirements.txt"
                ) from exc

            capture = self._open_camera(cv2, camera_index)
            if capture is None or not capture.isOpened():
                raise RuntimeError(
                    f"camera {camera_index} could not be opened (it may be in "
                    "use or blocked by Windows camera privacy settings)"
                )
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            hands, detect_hands = _create_hand_tracker(mp)
            screen_width, screen_height = pyautogui.size()
            pyautogui.FAILSAFE = True
            pyautogui.FAILSAFE_POINTS = [
                (0, 0), (0, screen_height - 1),
                (screen_width - 1, 0),
                (screen_width - 1, screen_height - 1),
            ]

            with self._lock:
                self._capture = capture
                self._running = True
            ready.set()

            smooth_x, smooth_y = map(float, pyautogui.position())
            pinch = _PinchLatch()
            scroll = _ScrollGesture()
            volume = _VolumeGesture()
            last_click = 0.0

            while not stop_event.is_set():
                ok, frame = capture.read()
                if not ok or frame is None:
                    if stop_event.is_set():
                        break
                    time.sleep(0.02)
                    continue

                # A mirrored feed makes hand movement and pointer movement
                # agree with what a user intuitively sees in a webcam preview.
                frame = cv2.flip(frame, 1)
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                rgb.flags.writeable = False
                detected = detect_hands(rgb)
                if not detected:
                    pinch.reset()
                    scroll.reset()
                    volume.reset()
                    continue

                landmarks = detected[0]
                frame_height, frame_width = frame.shape[:2]
                thumb_tip = landmarks[4]
                index_tip = landmarks[8]
                middle_tip = landmarks[12]
                triple_x = ((float(thumb_tip.x) + float(index_tip.x)
                             + float(middle_tip.x)) / 3.0)
                volume_pose = _volume_clutch_pose(
                    landmarks, frame_width, frame_height, volume.active)
                volume_steps = volume.update(volume_pose, triple_x)
                if volume.active:
                    # Volume mode owns the three-finger clutch. Freeze pointer,
                    # scroll and click processing until the pose is released.
                    pinch.reset()
                    scroll.reset()
                    if volume_steps:
                        key = "volumeup" if volume_steps > 0 else "volumedown"
                        pyautogui.press(key, presses=abs(volume_steps),
                                         interval=0.0, _pause=False)
                    continue

                joined_ratio = _finger_gap_ratio(
                    landmarks, 8, 12, frame_width, frame_height)
                joined_y = (float(index_tip.y) + float(middle_tip.y)) / 2.0
                scroll_steps = scroll.update(joined_ratio, joined_y)
                if scroll.active:
                    # Scroll mode is exclusive: freeze the pointer and prevent
                    # a thumb position from accidentally arming/clicking.
                    pinch.reset()
                    if scroll_steps:
                        pyautogui.scroll(scroll_steps, _pause=False)
                    continue

                index_tip = landmarks[8]
                target_x = _map_axis(index_tip.x, screen_width)
                target_y = _map_axis(index_tip.y, screen_height)
                smooth_x += (target_x - smooth_x) * _SMOOTHING
                smooth_y += (target_y - smooth_y) * _SMOOTHING
                pyautogui.moveTo(round(smooth_x), round(smooth_y),
                                 duration=0, _pause=False)

                ratio = _pinch_ratio(landmarks, frame_width, frame_height)
                now = time.monotonic()
                if (pinch.update(ratio)
                        and now - last_click >= _MIN_CLICK_INTERVAL):
                    pyautogui.click(button="left", _pause=False)
                    last_click = now

        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)
            try:
                from ..utils import logging as log
                log.warn(f"mouse_control stopped: {exc}")
            except Exception:
                pass
        finally:
            if hands is not None:
                try:
                    hands.close()
                except Exception:
                    pass
            if capture is not None:
                try:
                    capture.release()
                except Exception:
                    pass
            with self._lock:
                self._running = False
                self._capture = None
            ready.set()

    @staticmethod
    def _open_camera(cv2: Any, camera_index: int) -> Any:
        """Prefer DirectShow on Windows, then fall back to OpenCV default."""
        capture = None
        if os.name == "nt" and hasattr(cv2, "CAP_DSHOW"):
            capture = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
            if capture.isOpened():
                return capture
            capture.release()
        return cv2.VideoCapture(camera_index)


_CONTROLLER = HandMouseController()


def set_enabled(enabled: bool, camera_index: int = 0) -> tuple[bool, str]:
    """Turn hand mouse control on/off; called by the Jarvis action handler."""
    if enabled:
        return _CONTROLLER.start(camera_index)
    return _CONTROLLER.stop()


def is_enabled() -> bool:
    return _CONTROLLER.is_enabled()


atexit.register(_CONTROLLER.stop)
