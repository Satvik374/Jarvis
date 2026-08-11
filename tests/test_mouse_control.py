"""Tests for the toggleable camera hand-mouse action."""

from types import SimpleNamespace

from jarvis.tools import mouse_control, registry
from jarvis.tools.schema import ACTIONS_BY_NAME


def test_registered_in_schema_and_registry():
    assert "mouse_control" in ACTIONS_BY_NAME
    assert "mouse_control" in registry._HANDLERS


def test_registry_toggles_controller(monkeypatch):
    calls = []

    def fake_set_enabled(enabled, camera_index=0):
        calls.append((enabled, camera_index))
        return True, "changed"

    monkeypatch.setattr(mouse_control, "set_enabled", fake_set_enabled)
    on = registry.execute("mouse_control", {"enabled": True, "camera": 2},
                          None, None)
    off = registry.execute("mouse_control", {"enabled": "off"}, None, None)

    assert on.ok and off.ok
    assert not on.needs_observe and not off.needs_observe
    assert calls == [(True, 2), (False, 0)]


def test_registry_rejects_ambiguous_toggle():
    result = registry.execute("mouse_control", {"enabled": "maybe"},
                              None, None)
    assert not result.ok
    assert "true or false" in result.message


def test_camera_coordinate_mapping_uses_inner_active_region():
    assert mouse_control._map_axis(0.08, 1920) == 3
    assert mouse_control._map_axis(0.50, 1920) in {959, 960}
    assert mouse_control._map_axis(0.92, 1920) == 1916


def test_pinch_latch_emits_only_once_until_release():
    latch = mouse_control._PinchLatch()
    assert not latch.update(0.80)     # separated fingers arm the click
    assert latch.update(0.20)         # pinch-down clicks once
    assert not latch.update(0.18)     # holding the pinch does not repeat
    assert not latch.update(0.45)     # hysteresis prevents noisy re-arming
    assert not latch.update(0.80)     # clear release re-arms
    assert latch.update(0.25)         # a new pinch clicks once


def test_pinch_ratio_is_relative_to_palm_width():
    points = [SimpleNamespace(x=0.0, y=0.0) for _ in range(21)]
    points[4] = SimpleNamespace(x=0.20, y=0.20)
    points[8] = SimpleNamespace(x=0.22, y=0.20)
    points[5] = SimpleNamespace(x=0.20, y=0.40)
    points[17] = SimpleNamespace(x=0.40, y=0.40)

    ratio = mouse_control._pinch_ratio(points, 1000, 500)
    assert 0.09 < ratio < 0.11


def test_finger_gap_ratio_supports_index_middle_scroll_gesture():
    points = [SimpleNamespace(x=0.0, y=0.0) for _ in range(21)]
    points[8] = SimpleNamespace(x=0.30, y=0.20)
    points[12] = SimpleNamespace(x=0.32, y=0.20)
    points[5] = SimpleNamespace(x=0.20, y=0.40)
    points[17] = SimpleNamespace(x=0.40, y=0.40)

    ratio = mouse_control._finger_gap_ratio(points, 8, 12, 1000, 500)
    assert 0.09 < ratio < 0.11


def test_triple_pinch_ratio_requires_thumb_near_both_fingertips():
    points = [SimpleNamespace(x=0.0, y=0.0) for _ in range(21)]
    points[4] = SimpleNamespace(x=0.20, y=0.20)   # thumb
    points[8] = SimpleNamespace(x=0.22, y=0.20)   # index
    points[12] = SimpleNamespace(x=0.20, y=0.22)  # middle
    points[5] = SimpleNamespace(x=0.20, y=0.40)
    points[17] = SimpleNamespace(x=0.40, y=0.40)

    ratio = mouse_control._triple_pinch_ratio(points, 1000, 500)
    assert 0.09 < ratio < 0.11

    points[12] = SimpleNamespace(x=0.60, y=0.20)
    assert mouse_control._triple_pinch_ratio(points, 1000, 500) > 1.9


def _volume_pose_points():
    points = [SimpleNamespace(x=0.0, y=0.0) for _ in range(21)]
    points[0] = SimpleNamespace(x=0.50, y=0.85)   # wrist
    points[4] = SimpleNamespace(x=0.18, y=0.50)   # open thumb
    points[5] = SimpleNamespace(x=0.40, y=0.62)   # index mcp
    points[6] = SimpleNamespace(x=0.40, y=0.44)   # index pip
    points[8] = SimpleNamespace(x=0.40, y=0.18)   # index tip
    points[10] = SimpleNamespace(x=0.56, y=0.44)  # middle pip
    points[12] = SimpleNamespace(x=0.56, y=0.18)  # middle tip
    points[14] = SimpleNamespace(x=0.66, y=0.65)  # ring pip
    points[16] = SimpleNamespace(x=0.65, y=0.72)  # folded ring tip
    points[17] = SimpleNamespace(x=0.74, y=0.64)  # pinky mcp
    points[18] = SimpleNamespace(x=0.76, y=0.68)  # pinky pip
    points[20] = SimpleNamespace(x=0.75, y=0.74)  # folded pinky tip
    return points


def test_three_finger_volume_pose_matches_open_thumb_index_middle():
    points = _volume_pose_points()

    assert mouse_control._three_finger_volume_pose(points, 1000, 500)


def test_three_finger_volume_pose_requires_folded_ring_and_pinky():
    points = _volume_pose_points()
    points[16] = SimpleNamespace(x=0.66, y=0.18)

    assert not mouse_control._three_finger_volume_pose(points, 1000, 500)


def test_three_finger_volume_pose_does_not_capture_joined_scroll_pair():
    points = _volume_pose_points()
    points[12] = SimpleNamespace(x=0.42, y=0.18)

    assert not mouse_control._three_finger_volume_pose(points, 1000, 500)


def test_scroll_gesture_moves_in_natural_direction_without_jitter_repeats():
    gesture = mouse_control._ScrollGesture()

    assert gesture.update(0.20, 0.50) == 0  # joining establishes the anchor
    assert gesture.active
    assert gesture.update(0.20, 0.46) == 1  # fingers up -> scroll up
    assert gesture.update(0.20, 0.46) == 0  # holding still does not repeat
    assert gesture.update(0.20, 0.54) == -2  # fingers down -> scroll down


def test_scroll_gesture_uses_join_release_hysteresis():
    gesture = mouse_control._ScrollGesture()

    assert gesture.update(0.20, 0.50) == 0
    assert gesture.active
    assert gesture.update(0.40, 0.50) == 0  # small gap remains in scroll mode
    assert gesture.active
    assert gesture.update(0.60, 0.50) == 0  # clear separation exits it
    assert not gesture.active
    assert gesture.anchor_y is None


def test_volume_gesture_tracks_horizontal_motion_without_repeat_jitter():
    gesture = mouse_control._VolumeGesture()

    assert gesture.update(True, 0.50) == 0  # pose establishes the anchor
    assert gesture.active
    assert gesture.update(True, 0.54) == 1  # right raises volume
    assert gesture.update(True, 0.54) == 0  # holding still does not repeat
    assert gesture.update(True, 0.46) == -2  # left lowers volume


def test_volume_gesture_releases_after_brief_pose_loss():
    gesture = mouse_control._VolumeGesture()

    assert gesture.update(True, 0.50) == 0
    assert gesture.active
    assert gesture.update(False, 0.50) == 0
    assert gesture.active
    assert gesture.update(False, 0.50) == 0
    assert gesture.active
    assert gesture.update(False, 0.50) == 0
    assert not gesture.active
    assert gesture.anchor_x is None
