"""Audio & Media Stream Inspector Engine for Jarvis.

Enables inspecting audio/media stream metadata (WAV, MP3, FLAC, MP4), computing
waveform RMS energy envelopes, detecting speech/silence intervals, and slicing
audio clips natively without external heavy dependencies.
"""

from __future__ import annotations

import json
import math
import os
import struct
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .files import _expand, _within


# --------------------------------------------------------------------------- #
# Metadata Parsers
# --------------------------------------------------------------------------- #

def get_wav_info(path: Path) -> Dict[str, Any]:
    with wave.open(str(path), "rb") as w:
        channels = w.getnchannels()
        sampwidth = w.getsampwidth()
        framerate = w.getframerate()
        nframes = w.getnframes()
        duration = round(nframes / float(framerate), 3) if framerate else 0.0

        return {
            "format": "WAV",
            "channels": channels,
            "sample_width_bytes": sampwidth,
            "bit_depth": sampwidth * 8,
            "sample_rate_hz": framerate,
            "total_frames": nframes,
            "duration_sec": duration,
            "file_size_bytes": path.stat().st_size,
        }


def get_flac_info(path: Path) -> Dict[str, Any]:
    data = path.read_bytes()
    if len(data) < 42 or data[:4] != b"fLaC":
        return {"format": "FLAC", "file_size_bytes": len(data), "error": "Invalid FLAC header"}

    # STREAMINFO block is at offset 8 (after 4-byte fLaC and 4-byte block header)
    streaminfo = data[8:42]
    # Parse 18-35 bytes of streaminfo
    # bits: sample_rate (20 bits), channels (3 bits), bits_per_sample (5 bits), total_samples (36 bits)
    b18_25 = streaminfo[10:18]
    val = int.from_bytes(b18_25, "big")

    sample_rate = (val >> 44) & 0xFFFFF
    channels = ((val >> 41) & 0x7) + 1
    bits_per_sample = ((val >> 36) & 0x1F) + 1
    total_samples = val & 0xFFFFFFFFF

    duration = round(total_samples / float(sample_rate), 3) if sample_rate else 0.0

    return {
        "format": "FLAC",
        "channels": channels,
        "bit_depth": bits_per_sample,
        "sample_rate_hz": sample_rate,
        "total_samples": total_samples,
        "duration_sec": duration,
        "file_size_bytes": len(data),
    }


def get_mp3_info(path: Path) -> Dict[str, Any]:
    data = path.read_bytes()
    size = len(data)
    if size < 128:
        return {"format": "MP3", "file_size_bytes": size, "error": "File too small"}

    # Check ID3v2 header
    offset = 0
    if data.startswith(b"ID3"):
        id3_len = ((data[6] & 0x7F) << 21) | ((data[7] & 0x7F) << 14) | ((data[8] & 0x7F) << 7) | (data[9] & 0x7F)
        offset = 10 + id3_len

    # Find first MP3 frame sync (0xFFE or 0xFFF)
    frame_found = False
    sample_rate = 44100
    bitrate_kbps = 128
    channels = 2

    # Sample rates table for MPEG-1 Layer 3
    sr_table = {0: 44100, 1: 48000, 2: 32000}
    br_table = {1: 32, 2: 40, 3: 48, 4: 56, 5: 64, 6: 80, 7: 96, 8: 112, 9: 128, 10: 160, 11: 192, 12: 224, 13: 256, 14: 320}

    for i in range(offset, min(offset + 8192, size - 4)):
        if data[i] == 0xFF and (data[i + 1] & 0xE0) == 0xE0:
            b1 = data[i + 1]
            b2 = data[i + 2]
            b3 = data[i + 3]

            sr_idx = (b2 >> 2) & 0x03
            br_idx = (b2 >> 4) & 0x0F
            ch_mode = (b3 >> 6) & 0x03

            if sr_idx in sr_table and br_idx in br_table:
                sample_rate = sr_table[sr_idx]
                bitrate_kbps = br_table[br_idx]
                channels = 1 if ch_mode == 3 else 2
                frame_found = True
                break

    est_duration = round((size * 8) / (bitrate_kbps * 1000.0), 3) if bitrate_kbps else 0.0

    return {
        "format": "MP3",
        "channels": channels,
        "sample_rate_hz": sample_rate,
        "bitrate_kbps": bitrate_kbps,
        "estimated_duration_sec": est_duration,
        "file_size_bytes": size,
    }


def get_media_info(path: Path) -> Dict[str, Any]:
    ext = path.suffix.lower()
    if ext == ".wav":
        return get_wav_info(path)
    elif ext == ".flac":
        return get_flac_info(path)
    elif ext in (".mp3", ".m4a", ".aac"):
        return get_mp3_info(path)
    else:
        return {
            "format": ext.upper().lstrip("."),
            "file_size_bytes": path.stat().st_size,
            "filename": path.name,
        }


# --------------------------------------------------------------------------- #
# Waveform & Silence Analysis
# --------------------------------------------------------------------------- #

def compute_waveform(path: Path, num_points: int = 40) -> Dict[str, Any]:
    """Compute normalized RMS energy envelope and peak amplitude."""
    if path.suffix.lower() != ".wav":
        return {"error": "Waveform analysis requires a .wav audio file"}

    with wave.open(str(path), "rb") as w:
        nframes = w.getnframes()
        channels = w.getnchannels()
        sampwidth = w.getsampwidth()
        framerate = w.getframerate()

        if sampwidth not in (1, 2):
            return {"error": f"Unsupported sample width ({sampwidth} bytes) - only 8-bit or 16-bit supported"}

        chunk_size = max(1, nframes // num_points)
        envelope: List[float] = []
        peak = 0.0

        for _ in range(num_points):
            frames = w.readframes(chunk_size)
            if not frames:
                break

            if sampwidth == 2:
                count = len(frames) // 2
                samples = struct.unpack(f"<{count}h", frames)
                # Normalize 16-bit to [-1.0, 1.0]
                sq_sum = sum((s / 32768.0) ** 2 for s in samples)
                rms = math.sqrt(sq_sum / count) if count else 0.0
                peak = max(peak, max(abs(s / 32768.0) for s in samples) if samples else 0.0)
            else:
                samples = list(frames)
                sq_sum = sum(((s - 128) / 128.0) ** 2 for s in samples)
                rms = math.sqrt(sq_sum / len(samples)) if samples else 0.0
                peak = max(peak, max(abs((s - 128) / 128.0) for s in samples) if samples else 0.0)

            envelope.append(round(rms, 4))

        return {
            "format": "WAV",
            "duration_sec": round(nframes / float(framerate), 3) if framerate else 0.0,
            "envelope": envelope,
            "peak_amplitude": round(peak, 4),
            "dynamic_range_db": round(20 * math.log10(peak + 1e-9), 2),
        }


def detect_silence(path: Path, threshold_db: float = -40.0, window_ms: int = 100) -> Dict[str, Any]:
    """Identify speech activity and silence timestamp boundaries."""
    if path.suffix.lower() != ".wav":
        return {"error": "Silence detection requires a .wav audio file"}

    with wave.open(str(path), "rb") as w:
        nframes = w.getnframes()
        channels = w.getnchannels()
        sampwidth = w.getsampwidth()
        framerate = w.getframerate()

        if sampwidth != 2:
            return {"error": "Silence detection currently supports 16-bit PCM WAV"}

        frames_per_window = int(framerate * (window_ms / 1000.0))
        silence_thresh_linear = 10 ** (threshold_db / 20.0)

        intervals: List[Dict[str, Any]] = []
        is_silent = True
        seg_start = 0.0
        cur_time = 0.0

        while True:
            frames = w.readframes(frames_per_window)
            if not frames:
                break

            count = len(frames) // 2
            samples = struct.unpack(f"<{count}h", frames)
            sq_sum = sum((s / 32768.0) ** 2 for s in samples)
            rms = math.sqrt(sq_sum / count) if count else 0.0

            silent_now = rms < silence_thresh_linear

            if silent_now != is_silent:
                intervals.append({
                    "type": "silence" if is_silent else "speech",
                    "start_sec": round(seg_start, 2),
                    "end_sec": round(cur_time, 2),
                    "duration_sec": round(cur_time - seg_start, 2),
                })
                is_silent = silent_now
                seg_start = cur_time

            cur_time += window_ms / 1000.0

        # Close last segment
        intervals.append({
            "type": "silence" if is_silent else "speech",
            "start_sec": round(seg_start, 2),
            "end_sec": round(cur_time, 2),
            "duration_sec": round(cur_time - seg_start, 2),
        })

        return {
            "file": path.name,
            "threshold_db": threshold_db,
            "total_segments": len(intervals),
            "segments": intervals[:30],
        }


def slice_wav(src_path: Path, dst_path: Path, start_sec: float, end_sec: float) -> str:
    """Extract a slice of audio from start_sec to end_sec into a new WAV file."""
    if src_path.suffix.lower() != ".wav":
        return "Audio slicing currently requires a .wav audio file"

    with wave.open(str(src_path), "rb") as src:
        framerate = src.getframerate()
        nframes = src.getnframes()
        sampwidth = src.getsampwidth()
        channels = src.getnchannels()

        start_frame = max(0, int(start_sec * framerate))
        end_frame = min(nframes, int(end_sec * framerate))

        if start_frame >= end_frame:
            return f"invalid slice range: start_sec ({start_sec}) >= end_sec ({end_sec})"

        num_frames = end_frame - start_frame
        src.setpos(start_frame)
        slice_data = src.readframes(num_frames)

        dst_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(dst_path), "wb") as dst:
            dst.setnchannels(channels)
            dst.setsampwidth(sampwidth)
            dst.setframerate(framerate)
            dst.writeframes(slice_data)

        out_duration = round(num_frames / float(framerate), 3)
        return f"sliced audio saved to {dst_path.name} (duration {out_duration}s)"


# --------------------------------------------------------------------------- #
# Main Entrypoint
# --------------------------------------------------------------------------- #

def media_intel(
    op: str = "info",
    path: str = "",
    target: str = "",
    start_sec: float = 0.0,
    end_sec: float = 0.0,
    threshold_db: float = -40.0,
    allow: tuple[str, ...] = (),
) -> str:
    """Inspect media metadata, waveform envelope, silence detection, or slice audio.

    Operations:
      - 'info': Inspect media stream parameters (duration, sample rate, channels, bit depth, bitrate).
      - 'waveform': Compute normalized RMS energy envelope and peak amplitude metrics.
      - 'silence': Detect speech vs silence timestamp intervals.
      - 'slice': Extract a segment of audio between start_sec and end_sec into target path.
    """
    if not path:
        return "media_intel requires a 'path' parameter"

    p = _expand(path)
    if not p.exists():
        return f"file not found: {p}"

    op_clean = (op or "info").strip().lower()

    if op_clean in ("info", "metadata", "inspect"):
        res = get_media_info(p)
        return json.dumps(res, indent=2)

    elif op_clean in ("waveform", "envelope", "energy"):
        res = compute_waveform(p)
        return json.dumps(res, indent=2)

    elif op_clean in ("silence", "vad", "intervals", "segments"):
        res = detect_silence(p, threshold_db=threshold_db)
        return json.dumps(res, indent=2)

    elif op_clean in ("slice", "cut", "trim", "clip"):
        dst = _expand(target) if target else p.with_name(f"{p.stem}_slice.wav")
        return slice_wav(p, dst, start_sec=float(start_sec or 0.0), end_sec=float(end_sec or 0.0))

    return f"unknown media_intel op '{op}' - supported: info, waveform, silence, slice"
