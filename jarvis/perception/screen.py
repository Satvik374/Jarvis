"""Screen capture.

Uses ``mss`` for fast, multi-monitor screenshots and falls back to Pillow's
``ImageGrab`` if mss is unavailable. All heavy imports are lazy so this module
can be imported on any machine.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import time


@contextmanager
def _hide_hud_for_capture():
    """Temporarily hide the Floating HUD so screen captures never capture the HUD overlay."""
    try:
        from ..hud import get_hud_controller
        controller = get_hud_controller()
        if controller and controller.overlay and controller.overlay._running:
            if controller.is_hud_visible():
                controller.hide_hud_sync(timeout=0.08)
                try:
                    yield
                finally:
                    controller.show_hud_sync(timeout=0.08)
                return
    except Exception:
        pass
    yield


@dataclass
class Screenshot:
    image: "object"          # PIL.Image.Image
    width: int
    height: int
    path: Path | None = None

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.image.save(path)
        self.path = path
        return path


def screen_size() -> tuple[int, int]:
    """Return the primary screen (width, height) in pixels."""
    try:
        import mss  # type: ignore

        with mss.mss() as sct:
            mon = sct.monitors[1]
            return mon["width"], mon["height"]
    except Exception:
        from PIL import ImageGrab  # type: ignore

        img = ImageGrab.grab()
        return img.size


def capture(monitor: int = 1) -> Screenshot:
    """Grab the current screen as a :class:`Screenshot`.

    ``monitor=1`` is the primary display in mss numbering (0 = all combined).
    Temporarily hides the Floating HUD overlay so screenshots never capture it.
    """
    with _hide_hud_for_capture():
        try:
            import mss  # type: ignore
            from PIL import Image  # type: ignore

            with mss.mss() as sct:
                mon = sct.monitors[monitor]
                raw = sct.grab(mon)
                img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
                return Screenshot(image=img, width=img.width, height=img.height)
        except Exception:
            pass

    try:
        from PIL import ImageGrab  # type: ignore

        img = ImageGrab.grab().convert("RGB")
        return Screenshot(image=img, width=img.width, height=img.height)
    except Exception:
        from PIL import Image  # type: ignore
        img = Image.new("RGB", (1920, 1080), color=(18, 24, 38))
        return Screenshot(image=img, width=img.width, height=img.height)


def capture_to(path: str | Path, monitor: int = 1) -> Screenshot:
    shot = capture(monitor)
    shot.save(path)
    return shot


def timestamped_name(prefix: str = "shot") -> str:
    return f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}_{int(time.time()*1000) % 1000:03d}.png"
