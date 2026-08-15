"""Unit tests for Live Screen & Webcam "See What I See" Vision Engine."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch
from PIL import Image

from jarvis.config import Config
from jarvis.perception.elements import Observation
from jarvis.perception.live_vision import (
    LiveVisionEngine,
    capture_live_frame,
    get_live_vision,
    see,
)
from jarvis.tools.registry import execute


class TestLiveVision(unittest.TestCase):
    def setUp(self):
        self.vision = LiveVisionEngine()

    def test_capture_screen(self):
        """Test desktop screen capture returns a valid PIL RGB image."""
        img = self.vision.capture_screen()
        self.assertIsInstance(img, Image.Image)
        self.assertEqual(img.mode, "RGB")
        self.assertGreater(img.width, 0)
        self.assertGreater(img.height, 0)

    def test_fuse_views_with_webcam(self):
        """Test dual-view fusion with screen + webcam."""
        screen = Image.new("RGB", (1920, 1080), color=(30, 40, 50))
        webcam = Image.new("RGB", (640, 480), color=(100, 120, 140))

        fused = self.vision.fuse_views(screen, webcam)
        self.assertIsInstance(fused, Image.Image)
        self.assertEqual(fused.size, (1600, 900))

    def test_fuse_views_without_webcam(self):
        """Test dual-view fusion fallback when webcam is offline."""
        screen = Image.new("RGB", (1920, 1080), color=(30, 40, 50))
        fused = self.vision.fuse_views(screen, None)
        self.assertIsInstance(fused, Image.Image)
        self.assertEqual(fused.size, (1600, 900))

    def test_get_latest_frame_bytes(self):
        """Test JPEG encoding for Web UI live streaming."""
        jpeg_bytes = self.vision.get_latest_frame_bytes(source="screen")
        self.assertIsInstance(jpeg_bytes, bytes)
        self.assertTrue(jpeg_bytes.startswith(b"\xff\xd8"))  # JPEG magic bytes

        fused_bytes = self.vision.get_latest_frame_bytes(source="both")
        self.assertIsInstance(fused_bytes, bytes)
        self.assertTrue(fused_bytes.startswith(b"\xff\xd8"))

    def test_analyze_with_mock_brain(self):
        """Test perceptual analysis with mock vision brain."""
        mock_brain = MagicMock()
        mock_brain.complete.return_value = "I see a VS Code editor with Python code on screen, and a desk with a notebook on webcam."

        result = self.vision.analyze(source="both", prompt="What do you see?", brain=mock_brain)
        self.assertIn("VS Code", result)
        self.assertIn("notebook", result)
        mock_brain.complete.assert_called_once()

    def test_describe_scene(self):
        """Test structured scene perception."""
        mock_brain = MagicMock()
        mock_brain.complete.return_value = "1. Screen: Browser open. 2. Environment: Desk with coffee mug."

        scene = self.vision.describe_scene(source="both", brain=mock_brain)
        self.assertIsInstance(scene, dict)
        self.assertEqual(scene["source"], "both")
        self.assertIn("coffee mug", scene["analysis"])

    def test_tool_action_see(self):
        """Test agent action 'see' execution via registry."""
        cfg = Config()
        obs = Observation(elements=[], screen_size=(1920, 1080), active_window="Test")

        with patch.object(LiveVisionEngine, "analyze", return_value="Live visual inspection: Code editor active."):
            res = execute("see", {"prompt": "What is on screen?", "source": "screen"}, obs, cfg)
            self.assertTrue(res.ok)
            self.assertIn("Code editor active", res.message)

    def test_convenience_helpers(self):
        """Test global see() and capture_live_frame() functions."""
        frame = capture_live_frame(source="screen")
        self.assertIsInstance(frame, bytes)
        self.assertTrue(frame.startswith(b"\xff\xd8"))


if __name__ == "__main__":
    unittest.main()
