"""Low-latency full-duplex audio stream for Gemini Live Voice."""

from __future__ import annotations

import collections
import math
import queue
import struct
import threading
import time
from typing import Callable

from ..utils import logging as log


class LiveAudioStream:
    """Manages full-duplex microphone capture (16kHz) and speaker output (24kHz)."""

    def __init__(
        self,
        rate_in: int = 16000,
        rate_out: int = 24000,
        chunk_ms: int = 50,
        on_audio_in: Callable[[bytes], None] | None = None,
        on_barge_in: Callable[[], None] | None = None,
        barge_in_sensitivity: float = 0.5,
        barge_in_hold: int = 2,
    ):
        self.rate_in = rate_in
        self.rate_out = rate_out
        self.chunk_ms = chunk_ms
        self.chunk_samples_in = int(rate_in * chunk_ms / 1000)
        self.chunk_samples_out = int(rate_out * chunk_ms / 1000)
        self.on_audio_in = on_audio_in
        self.on_barge_in = on_barge_in
        # 0.1 is strict (requires louder speech); 1.0 is more responsive.
        self.barge_in_sensitivity = max(0.1, min(1.0, float(barge_in_sensitivity)))
        self.barge_in_hold_required = max(1, int(barge_in_hold))

        self._in_stream = None
        self._out_stream = None
        self._is_running = False
        self._is_playing = False
        self._play_queue: queue.Queue[bytes | None] = queue.Queue(maxsize=100)
        self._playback_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()

        # Barge-in VAD state
        self._noise_floor = 300.0
        self._barge_in_hold = 0
        self._muted = False

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def is_playing(self) -> bool:
        return self._is_playing

    def set_muted(self, muted: bool) -> None:
        """Mute/unmute microphone streaming."""
        self._muted = muted

    def start(self) -> bool:
        """Start microphone and playback streams."""
        with self._lock:
            if self._is_running:
                return True
            try:
                import sounddevice as sd
            except ImportError:
                log.warn("sounddevice not available for live audio stream.")
                return False

            self._is_running = True
            self._cancel_event.clear()

            # Start playback worker thread
            self._playback_thread = threading.Thread(
                target=self._playback_loop,
                daemon=True,
                name="jarvis-live-playback",
            )
            self._playback_thread.start()

            # Start microphone input stream
            try:
                self._in_stream = sd.RawInputStream(
                    samplerate=self.rate_in,
                    channels=1,
                    dtype="int16",
                    blocksize=self.chunk_samples_in,
                    callback=self._mic_callback,
                )
                self._in_stream.start()
                return True
            except Exception as exc:
                log.warn(f"Failed to open microphone input stream: {exc}")
                self.stop()
                return False

    def stop(self) -> None:
        """Stop all audio streaming and playback."""
        with self._lock:
            self._is_running = False
            self._cancel_event.set()
            if self._in_stream is not None:
                try:
                    self._in_stream.stop()
                    self._in_stream.close()
                except Exception:
                    pass
                self._in_stream = None

            # Clear playback queue
            self.clear_playback()
            try:
                self._play_queue.put_nowait(None)
            except Exception:
                pass

    def play_chunk(self, pcm_chunk: bytes) -> None:
        """Enqueue a PCM audio chunk for real-time playback."""
        if not self._is_running or not pcm_chunk:
            return
        try:
            self._play_queue.put(pcm_chunk, timeout=0.2)
        except queue.Full:
            # Drop oldest if queue gets congested
            try:
                self._play_queue.get_nowait()
                self._play_queue.put_nowait(pcm_chunk)
            except Exception:
                pass

    def clear_playback(self) -> None:
        """Instantly silence active playback and clear buffered output (Barge-in)."""
        self._cancel_event.set()
        while not self._play_queue.empty():
            try:
                self._play_queue.get_nowait()
            except Exception:
                break
        self._cancel_event.clear()
        self._is_playing = False

    def _mic_callback(self, indata, frames, time_info, status) -> None:
        if not self._is_running or self._muted:
            return

        raw_bytes = bytes(indata)
        if not raw_bytes:
            return

        # Calculate energy for VAD & Barge-in detection
        sample_count = len(raw_bytes) // 2
        if sample_count > 0:
            shorts = struct.unpack(f"<{sample_count}h", raw_bytes)
            energy = math.sqrt(sum(s * s for s in shorts) / sample_count)
            self._update_vad(energy)

        # Forward audio to callback
        if self.on_audio_in is not None:
            try:
                self.on_audio_in(raw_bytes)
            except Exception as exc:
                log.debug(f"Audio in callback error: {exc}")

    def _update_vad(self, energy: float) -> None:
        # Dynamic noise floor tracking
        if not self._is_playing:
            self._noise_floor = 0.95 * self._noise_floor + 0.05 * min(energy, 1000.0)
            self._barge_in_hold = 0
            return

        # While playing, detect user speech above speaker bleed
        base_threshold = max(2000.0, self._noise_floor * 5.0)
        # Preserve the former threshold at the default 0.5 sensitivity while
        # making the advertised configuration actually affect interruption.
        threshold = max(800.0, base_threshold * (1.5 - self.barge_in_sensitivity))
        if energy > threshold:
            self._barge_in_hold += 1
            if self._barge_in_hold >= self.barge_in_hold_required:
                # Sustained user voice while Jarvis is speaking -> Barge-in!
                self.clear_playback()
                if self.on_barge_in is not None:
                    try:
                        self.on_barge_in()
                    except Exception:
                        pass
                self._barge_in_hold = 0
        else:
            self._barge_in_hold = max(0, self._barge_in_hold - 1)

    def _playback_loop(self) -> None:
        try:
            import sounddevice as sd
            with sd.RawOutputStream(
                samplerate=self.rate_out,
                channels=1,
                dtype="int16",
                blocksize=self.chunk_samples_out,
            ) as out_stream:
                while self._is_running:
                    try:
                        chunk = self._play_queue.get(timeout=0.1)
                    except queue.Empty:
                        self._is_playing = False
                        continue

                    if chunk is None or not self._is_running:
                        break

                    if self._cancel_event.is_set():
                        self._is_playing = False
                        continue

                    self._is_playing = True
                    try:
                        out_stream.write(chunk)
                    except Exception as write_exc:
                        log.debug(f"Live playback write exception: {write_exc}")
        except Exception as exc:
            log.debug(f"Live playback loop ended: {exc}")
        finally:
            self._is_playing = False
