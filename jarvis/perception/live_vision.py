"""Live Screen & Webcam "See What I See" Multimodal Vision Engine for Jarvis.

Fuses real-time desktop screen capture with live physical webcam vision,
enabling Jarvis to perceive, reason about, and answer questions regarding both
the user's digital desktop environment and their physical real-world surroundings.
"""

from __future__ import annotations

import io
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from PIL import Image, ImageDraw, ImageFont

from ..utils import logging as log
from .screen import capture as capture_screen_raw


class LiveVisionEngine:
    """Multimodal perception engine for live desktop screen and webcam video."""

    def __init__(self):
        self._lock = threading.Lock()
        self._cam_cache: Dict[int, Any] = {}
        self._latest_screen: Optional[Image.Image] = None
        self._latest_webcam: Optional[Image.Image] = None
        self._latest_fused: Optional[Image.Image] = None
        self._last_capture_time: float = 0.0
        self._streaming: bool = False
        self._stream_thread: Optional[threading.Thread] = None
        self._stream_stop_event = threading.Event()

    # ------------------------------------------------------------------ #
    # 1. Capture Primitives (Screen & Webcam)
    # ------------------------------------------------------------------ #

    def capture_screen(self, monitor: int = 1) -> Image.Image:
        """Capture the current desktop screen as a PIL Image."""
        try:
            shot = capture_screen_raw(monitor=monitor)
            img = shot.image
            if img.mode != "RGB":
                img = img.convert("RGB")
        except Exception:
            img = Image.new("RGB", (1920, 1080), color=(18, 24, 38))
        with self._lock:
            self._latest_screen = img
            self._last_capture_time = time.time()
        return img

    def capture_webcam(self, camera_index: int = 0, warmup_frames: int = 2) -> Optional[Image.Image]:
        """Capture a single frame from the specified webcam index."""
        camera_index = max(0, min(9, int(camera_index)))
        try:
            import cv2  # type: ignore

            # Try to use DirectShow on Windows for fast startup
            cap = None
            if hasattr(cv2, "CAP_DSHOW"):
                cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
            if cap is None or not cap.isOpened():
                cap = cv2.VideoCapture(camera_index)

            if not cap.isOpened():
                log.warn(f"Webcam {camera_index} could not be opened (camera offline or blocked by privacy settings).")
                return None

            # Read frames
            frame = None
            for _ in range(max(1, warmup_frames)):
                ret, f = cap.read()
                if ret and f is not None:
                    frame = f

            cap.release()

            if frame is None:
                return None

            # Convert BGR (OpenCV) to RGB (Pillow)
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(rgb_frame)
            with self._lock:
                self._latest_webcam = img
                self._last_capture_time = time.time()
            return img
        except Exception as exc:
            log.warn(f"Webcam capture failed ({exc}).")
            return None

    # ------------------------------------------------------------------ #
    # 2. Dual-View Vision Fusion ("See What I See")
    # ------------------------------------------------------------------ #

    def capture_dual_view(self, camera_index: int = 0, monitor: int = 1) -> Tuple[Image.Image, Optional[Image.Image], Image.Image]:
        """Capture both desktop screen and webcam, returning (screen_img, webcam_img, fused_img)."""
        screen_img = self.capture_screen(monitor=monitor)
        webcam_img = self.capture_webcam(camera_index=camera_index)
        fused_img = self.fuse_views(screen_img, webcam_img)
        with self._lock:
            self._latest_fused = fused_img
        return screen_img, webcam_img, fused_img

    def fuse_views(self, screen_img: Image.Image, webcam_img: Optional[Image.Image] = None) -> Image.Image:
        """Compose screen and webcam views into a high-tech side-by-side or PIP visual frame with HUD telemetry."""
        target_w = 1600
        target_h = 900

        # Create dark futuristic canvas
        canvas = Image.new("RGB", (target_w, target_h), color=(8, 12, 20))
        draw = ImageDraw.Draw(canvas)

        if webcam_img is not None:
            # Side-by-side composition: Screen (65% width) + Webcam (35% width)
            scr_w = 1040
            scr_h = 760
            scr_resized = screen_img.resize((scr_w, scr_h), Image.Resampling.LANCZOS)
            canvas.paste(scr_resized, (30, 80))

            cam_w = 460
            cam_h = 345
            cam_resized = webcam_img.resize((cam_w, cam_h), Image.Resampling.LANCZOS)
            canvas.paste(cam_resized, (1100, 80))

            # Draw HUD bounding boxes & labels
            # Screen frame
            draw.rectangle([28, 78, 28 + scr_w + 2, 78 + scr_h + 2], outline=(0, 220, 255), width=2)
            draw.text((36, 56), "LIVE DESKTOP STREAM [CH-01]", fill=(0, 220, 255))

            # Webcam frame
            draw.rectangle([1098, 78, 1098 + cam_w + 2, 78 + cam_h + 2], outline=(0, 255, 170), width=2)
            draw.text((1106, 56), "LIVE USER WEBCAM [CH-02]", fill=(0, 255, 170))

            # Context telemetry box in bottom-right corner
            telemetry_box = [1100, 450, 1560, 840]
            draw.rectangle(telemetry_box, outline=(60, 90, 140), fill=(12, 18, 30), width=1)
            draw.text((1115, 465), "MULTIMODAL FUSION HUD", fill=(0, 220, 255))
            draw.text((1115, 495), f"TIMESTAMP: {time.strftime('%Y-%m-%d %H:%M:%S')}", fill=(180, 200, 220))
            draw.text((1115, 525), f"SCREEN RES: {screen_img.width}x{screen_img.height}", fill=(140, 160, 180))
            draw.text((1115, 555), f"WEBCAM RES: {webcam_img.width}x{webcam_img.height}", fill=(140, 160, 180))
            draw.text((1115, 585), "VISION MODE: SEE-WHAT-I-SEE", fill=(0, 255, 170))
            draw.text((1115, 615), "PERCEPTION: ACTIVE", fill=(0, 220, 255))

            # Draw corner crosshairs
            draw.line([(20, 20), (40, 20)], fill=(0, 220, 255), width=2)
            draw.line([(20, 20), (20, 40)], fill=(0, 220, 255), width=2)
            draw.line([(target_w - 40, 20), (target_w - 20, 20)], fill=(0, 220, 255), width=2)
            draw.line([(target_w - 20, 20), (target_w - 20, 40)], fill=(0, 220, 255), width=2)
        else:
            # Single Screen with HUD border
            scr_w = 1480
            scr_h = 760
            scr_resized = screen_img.resize((scr_w, scr_h), Image.Resampling.LANCZOS)
            canvas.paste(scr_resized, (60, 80))
            draw.rectangle([58, 78, 58 + scr_w + 2, 78 + scr_h + 2], outline=(0, 220, 255), width=2)
            draw.text((66, 56), "LIVE DESKTOP STREAM (WEBCAM OFFLINE)", fill=(0, 220, 255))
            draw.text((target_w - 300, 56), f"TIME: {time.strftime('%H:%M:%S')}", fill=(180, 200, 220))

        # Bottom HUD Ribbon
        draw.text((60, target_h - 35), "JARVIS SEE-WHAT-I-SEE MULTIMODAL PERCEPTION // REAL-TIME FUSION ACTIVE", fill=(100, 130, 160))
        return canvas

    # ------------------------------------------------------------------ #
    # 3. Multimodal Visual Reasoning & Analysis
    # ------------------------------------------------------------------ #

    def analyze(self, source: str = "both", prompt: str = "Describe what you see in detail.", brain: Optional[Any] = None, camera_index: int = 0) -> str:
        """Analyze live visual feed using the vision-capable LLM brain."""
        source = source.strip().lower()
        images: List[Image.Image] = []

        if source == "webcam":
            cam = self.capture_webcam(camera_index=camera_index)
            if cam is None:
                return "Webcam is unavailable or offline."
            images = [cam]
            context_note = "You are analyzing a live camera snapshot from the user's physical environment."
        elif source == "screen":
            scr = self.capture_screen()
            images = [scr]
            context_note = "You are analyzing a live desktop screenshot of the user's computer screen."
        else:  # "both" or "dual"
            scr, cam, fused = self.capture_dual_view(camera_index=camera_index)
            images = [fused]
            context_note = (
                "You are looking at a combined 'See What I See' visual feed: on the left is the user's "
                "live computer screen, and on the right is their live physical webcam view."
            )

        # Build brain if not provided
        if brain is None:
            from ..agent.brain import make_brain
            from ..config import Config
            cfg = Config.load()
            brain = make_brain(cfg.brain)

        system_instruction = (
            "You are JARVIS's Real-Time Visual Perception System. "
            f"{context_note}\n"
            "Analyze the image(s) with high precision, noting open applications, code, errors, documents, "
            "hardware, physical items, handwriting, or user actions. Answer clearly and concisely."
        )

        user_content = prompt or "What am I looking at right now?"
        messages = [{"role": "user", "content": user_content}]

        try:
            from ..agent.brain import complete_with_retry
            reply = complete_with_retry(brain, system_instruction, messages, image=images)
            return reply.strip()
        except Exception as exc:
            log.error(f"Visual analysis failed: {exc}")
            return f"Visual perception error: {exc}"

    def describe_scene(self, source: str = "both", brain: Optional[Any] = None) -> Dict[str, Any]:
        """Produce structured visual perception data about the live scene."""
        prompt = (
            "Summarize the scene in a structured format with:\n"
            "1. Digital Screen summary (active windows, apps, visible text/code)\n"
            "2. Physical Environment summary (room, desk items, user posture, objects held)\n"
            "3. Key focus or potential actionable items"
        )
        analysis_text = self.analyze(source=source, prompt=prompt, brain=brain)
        return {
            "timestamp": time.time(),
            "source": source,
            "analysis": analysis_text,
        }

    # ------------------------------------------------------------------ #
    # 4. Web UI & API Frame Encoding
    # ------------------------------------------------------------------ #

    def get_latest_frame_bytes(self, source: str = "both", format: str = "JPEG", quality: int = 80) -> bytes:
        """Encode the latest or fresh frame as binary JPEG bytes for streaming."""
        source = source.strip().lower()
        if source == "screen":
            img = self.capture_screen()
        elif source == "webcam":
            img = self.capture_webcam() or Image.new("RGB", (640, 480), color=(10, 15, 25))
        else:  # both / fused
            _, _, img = self.capture_dual_view()

        buf = io.BytesIO()
        img.save(buf, format=format, quality=quality)
        return buf.getvalue()


_GLOBAL_VISION: Optional[LiveVisionEngine] = None


def get_live_vision() -> LiveVisionEngine:
    global _GLOBAL_VISION
    if _GLOBAL_VISION is None:
        _GLOBAL_VISION = LiveVisionEngine()
    return _GLOBAL_VISION


def see(prompt: str = "What do you see?", source: str = "both", brain: Optional[Any] = None) -> str:
    """Convenience function to analyze live screen & webcam vision."""
    return get_live_vision().analyze(source=source, prompt=prompt, brain=brain)


def capture_live_frame(source: str = "both") -> bytes:
    """Convenience function to grab live JPEG frame bytes."""
    return get_live_vision().get_latest_frame_bytes(source=source)
