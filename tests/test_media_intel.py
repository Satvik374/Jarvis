"""Unit and integration tests for Audio & Media Stream Inspector Engine."""

from __future__ import annotations

import json
import math
import struct
import tempfile
import wave
from pathlib import Path

import pytest

from jarvis.config import load_config
from jarvis.perception.elements import Observation
from jarvis.tools import media_intel, registry
from jarvis.tools.schema import ACTIONS_BY_NAME


@pytest.fixture
def sample_wav_file():
    """Generate a 2-second 16-bit mono 16kHz WAV file with 1s tone and 1s silence."""
    with tempfile.TemporaryDirectory() as td:
        wav_path = Path(td) / "test_audio.wav"
        framerate = 16000
        duration = 2.0
        total_frames = int(framerate * duration)

        with wave.open(str(wav_path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(framerate)

            frames = bytearray()
            # 0.0 - 1.0s: 440 Hz Sine wave (speech/sound)
            # 1.0 - 2.0s: Silence
            for i in range(total_frames):
                t = i / float(framerate)
                if t < 1.0:
                    val = int(16000 * math.sin(2 * math.pi * 440 * t))
                else:
                    val = 0
                frames.extend(struct.pack("<h", val))

            w.writeframes(frames)

        yield wav_path


def test_schema_and_registry_registration():
    """Verify media_intel is in schema and registered in handlers."""
    assert "media_intel" in ACTIONS_BY_NAME
    action = ACTIONS_BY_NAME["media_intel"]
    assert action.category == "system"
    assert any(p.name == "op" for p in action.params)
    assert any(p.name == "path" for p in action.params)
    assert any(p.name == "target" for p in action.params)


def test_wav_metadata_inspection(sample_wav_file):
    """Test extracting WAV stream format, duration, channels, sample rate."""
    res = media_intel.media_intel(path=str(sample_wav_file), op="info")
    data = json.loads(res)

    assert data["format"] == "WAV"
    assert data["channels"] == 1
    assert data["sample_rate_hz"] == 16000
    assert data["bit_depth"] == 16
    assert data["duration_sec"] == 2.0


def test_waveform_rms_envelope(sample_wav_file):
    """Test computing waveform energy envelope."""
    res = media_intel.media_intel(path=str(sample_wav_file), op="waveform")
    data = json.loads(res)

    assert data["format"] == "WAV"
    assert "envelope" in data
    assert len(data["envelope"]) == 40
    # First half should have non-zero energy, second half near-zero
    first_half_avg = sum(data["envelope"][:20]) / 20.0
    second_half_avg = sum(data["envelope"][20:]) / 20.0
    assert first_half_avg > second_half_avg
    assert data["peak_amplitude"] > 0.3


def test_silence_detection(sample_wav_file):
    """Test speech vs silence boundary detection."""
    res = media_intel.media_intel(path=str(sample_wav_file), op="silence", threshold_db=-30.0)
    data = json.loads(res)

    assert "segments" in data
    assert data["total_segments"] >= 2
    types = [s["type"] for s in data["segments"]]
    assert "speech" in types
    assert "silence" in types


def test_audio_slicing(sample_wav_file):
    """Test cutting audio slice to new file."""
    slice_out = sample_wav_file.parent / "slice_0_to_1.wav"
    res = media_intel.media_intel(
        path=str(sample_wav_file),
        target=str(slice_out),
        op="slice",
        start_sec=0.0,
        end_sec=1.0,
    )

    assert "sliced audio saved" in res
    assert slice_out.exists()

    # Verify slice duration
    info_res = media_intel.media_intel(path=str(slice_out), op="info")
    info = json.loads(info_res)
    assert info["duration_sec"] == 1.0


def test_registry_execution(sample_wav_file):
    """Test executing media_intel through registry dispatcher."""
    cfg = load_config()
    obs = Observation(
        screenshot_path=None,
        elements=[],
        screen_size=(1920, 1080),
        active_window="Media",
    )

    res = registry.execute(
        name="media_intel",
        args={"path": str(sample_wav_file), "op": "info"},
        obs=obs,
        cfg=cfg,
    )
    assert res.ok is True
    assert '"format": "WAV"' in res.message
    assert res.needs_observe is False
