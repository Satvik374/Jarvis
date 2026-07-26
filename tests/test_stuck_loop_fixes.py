"""Fixes for the top failure modes found in 189 real trajectories:

  * stuck_loop (20% of runs): RESULT echoed translated screen pixels, the
    model copied them back, and values <=1000 were re-read as Gemini-normalized
    -> the same intended click landed elsewhere. Echoes are now model-space.
  * loop-detector evasion: jiggled coordinates ((300,655) -> (250,655)) never
    matched the exact-args signature. Coordinates are now bucketed (200px).
  * unhelpful click failures: stale element ids and corrupted coordinates now
    get distinct, actionable error messages.
  * brain error kills run (11%): 429/network errors now retry with a real
    backoff budget instead of dying after 2s+4s.
"""

import unittest
from unittest.mock import Mock, patch

from jarvis.agent.brain import complete_with_retry
from jarvis.agent.loop import _sig_args
from jarvis.config import Config
from jarvis.perception.elements import Element, Observation
from jarvis.tools.registry import _norm_to_pixels, _resolve_point, _target_desc


def _obs():
    return Observation(
        elements=[
            Element(0, "Edit", "Search", (10, 10, 200, 40), (105, 25)),
            Element(1, "Button", "Send", (400, 400, 460, 440), (430, 420)),
        ],
        screen_size=(1920, 1080),
    )


def _gemini_cfg():
    cfg = Config()
    cfg.brain.backend = "gemini"
    cfg.brain.use_vision = True
    return cfg


class ResolvePointTests(unittest.TestCase):
    def setUp(self):
        self.obs = _obs()

    def test_element_click_resolves(self):
        pt, err = _resolve_point({"element": 1}, self.obs)
        self.assertEqual(pt, (430, 420))
        self.assertEqual(err, "")

    def test_stale_element_id_names_valid_range(self):
        pt, err = _resolve_point({"element": 673}, self.obs)
        self.assertIsNone(pt)
        self.assertIn("673", err)
        self.assertIn("0-1", err)          # tells the model the valid ids

    def test_corrupt_coordinate_says_off_screen(self):
        # real glitch from trajectories: digit-duplicated y like 896896
        pt, err = _resolve_point({"x": 350, "y": 896896}, self.obs)
        self.assertIsNone(pt)
        self.assertIn("outside", err)
        self.assertIn("element id", err)   # tells the model what to do instead

    def test_no_target_is_explained(self):
        pt, err = _resolve_point({}, self.obs)
        self.assertIsNone(pt)
        self.assertIn("element id", err)

    def test_gemini_normalized_coords_are_denormalized(self):
        cfg = _gemini_cfg()
        pt, err = _resolve_point({"x": 500, "y": 500}, self.obs, cfg)
        self.assertEqual(pt, (960, 540))

    def test_pixel_coords_pass_through_without_gemini(self):
        pt, err = _resolve_point({"x": 700, "y": 700}, self.obs)
        self.assertEqual(pt, (700, 700))

    def test_snap_to_nearby_element(self):
        pt, err = _resolve_point({"x": 440, "y": 430}, self.obs)
        self.assertEqual(pt, (430, 420))   # snapped to Send's exact centre

    def test_copied_element_coords_beat_normalization(self):
        # Model copies an element's centre (430,420) from the list into raw
        # x/y under Gemini vision: it must click THAT element, not be
        # re-normalized to (826,454).
        pt, err = _resolve_point({"x": 430, "y": 420}, self.obs, _gemini_cfg())
        self.assertEqual(pt, (430, 420))


class NormToPixelsTests(unittest.TestCase):
    def test_values_above_1000_pass_through(self):
        self.assertEqual(_norm_to_pixels(1517, 780, _obs(), _gemini_cfg()),
                         (1517, 780))

    def test_no_cfg_means_no_conversion(self):
        self.assertEqual(_norm_to_pixels(500, 500, _obs(), None), (500, 500))


class TargetDescTests(unittest.TestCase):
    def test_element_click_echoes_label_not_pixels(self):
        desc = _target_desc({"element": 1}, _obs())
        self.assertIn('"Send"', desc)
        self.assertNotIn("430", desc)      # no pixel echo to copy back

    def test_raw_click_echoes_models_own_values(self):
        # The model said (838,780) normalized; the click landed at (1609,842)
        # pixels. Echoing 1609/842 poisons the model's coordinate space.
        desc = _target_desc({"x": 838, "y": 780}, _obs())
        self.assertEqual(desc, "(838,780)")


class SigArgsTests(unittest.TestCase):
    def test_jiggled_coords_share_a_bucket(self):
        # real stuck-loop jiggle: (300,655) -> (250,655) -> (300,655)
        self.assertEqual(_sig_args({"x": 300, "y": 655}),
                         _sig_args({"x": 250, "y": 655}))

    def test_distant_coords_differ(self):
        self.assertNotEqual(_sig_args({"x": 100, "y": 100}),
                            _sig_args({"x": 900, "y": 900}))

    def test_non_coord_args_untouched(self):
        self.assertEqual(_sig_args({"text": "hi", "element": 5}),
                         {"text": "hi", "element": 5})


class RetryBudgetTests(unittest.TestCase):
    @patch("jarvis.agent.brain.time.sleep")
    def test_rate_limit_gets_extended_budget(self, _sleep):
        brain = Mock()
        brain.complete.side_effect = [RuntimeError("HTTP 429")] * 4 + ["ok"]
        out = complete_with_retry(brain, "sys", [], tries=3)
        self.assertEqual(out, "ok")        # survived past the old 3-try limit
        self.assertEqual(brain.complete.call_count, 5)

    @patch("jarvis.agent.brain.time.sleep")
    def test_unknown_error_keeps_short_budget(self, _sleep):
        brain = Mock()
        brain.complete.side_effect = RuntimeError("bad key")
        with self.assertRaises(RuntimeError):
            complete_with_retry(brain, "sys", [], tries=3)
        self.assertEqual(brain.complete.call_count, 3)

    @patch("jarvis.agent.brain.time.sleep")
    def test_backoff_grows_for_transient(self, sleep):
        brain = Mock()
        brain.complete.side_effect = [RuntimeError("Read timed out."),
                                      RuntimeError("Read timed out."), "ok"]
        complete_with_retry(brain, "sys", [])
        delays = [c.args[0] for c in sleep.call_args_list]
        self.assertEqual(delays, [5, 10])


if __name__ == "__main__":
    unittest.main()
