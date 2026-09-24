"""Microphone capture into an in-memory WAV."""

from __future__ import annotations

import io
import logging
import wave
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)

SAMPLE_WIDTH = 2  # int16


@dataclass(frozen=True)
class Clip:
    """A finished recording, ready to send."""

    wav: bytes
    duration: float

    def __bool__(self) -> bool:
        return self.duration > 0


class Recorder:
    """Records mono int16 audio between start() and stop()."""

    def __init__(self, sample_rate: int = 16000, channels: int = 1) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self._frames: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._on_frame: Callable[[np.ndarray], None] | None = None

    @property
    def active(self) -> bool:
        return self._stream is not None

    def start(self, on_frame: Callable[[np.ndarray], None] | None = None) -> None:
        """Open the mic. `on_frame` sees every block as it arrives.

        The tap is per-recording, not per-recorder: a streaming session lives
        for exactly one hold of the hotkey, and leaving a stale callback bound
        would feed the next dictation into a closed socket.
        """
        self._frames = []
        self._on_frame = on_frame
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="int16",
            callback=self._on_audio,
        )
        self._stream.start()

    def stop(self) -> Clip:
        stream, self._stream = self._stream, None
        self._on_frame = None
        if stream is not None:
            stream.stop()
            stream.close()
        if not self._frames:
            return Clip(b"", 0.0)
        samples = np.concatenate(self._frames)
        self._frames = []
        return Clip(self._to_wav(samples), len(samples) / self.sample_rate)

    def _on_audio(self, indata, frames, time_info, status) -> None:
        if status:
            log.warning("audio status: %s", status)
        block = indata.copy()
        self._frames.append(block)
        tap = self._on_frame
        if tap is None:
            return
        # This runs on PortAudio's callback thread. An exception here would
        # kill the stream and end the recording, so a broken tap costs the
        # live transcript and nothing else — the clip is already captured.
        try:
            tap(block)
        except Exception:
            log.exception("audio tap failed")
            self._on_frame = None

    def _to_wav(self, samples: np.ndarray) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            wav.setnchannels(self.channels)
            wav.setsampwidth(SAMPLE_WIDTH)
            wav.setframerate(self.sample_rate)
            wav.writeframes(samples.tobytes())
        return buf.getvalue()
