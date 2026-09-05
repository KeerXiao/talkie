"""Wires capture, transcription and pasting into a push-to-talk loop."""

from __future__ import annotations

import logging
import threading

from talkie.audio import Clip, Recorder
from talkie.client import ClientError, OpenRouterClient, TranscriptionClient
from talkie.config import Config
from talkie.hotkey import ChordListener, parse_hotkey
from talkie.paste import Paster
from talkie.permissions import ACCESSIBILITY_HINT, accessibility_trusted
from talkie.sound import Player

log = logging.getLogger(__name__)


class Talkie:
    """Hold the chord to record; release to transcribe and paste.

    The chord callbacks run on the listener thread and stay cheap; the network
    round-trip happens on a worker so the hotkey never blocks.
    """

    def __init__(
        self,
        config: Config,
        recorder: Recorder | None = None,
        client: TranscriptionClient | None = None,
        paster: Paster | None = None,
        sound: Player | None = None,
    ) -> None:
        self.config = config
        self.chord = parse_hotkey(config.hotkey)
        self.recorder = recorder or Recorder(config.sample_rate, config.channels)
        self.client = client or OpenRouterClient(
            api_key=config.api_key,
            model=config.model,
            language=config.language,
            timeout=config.request_timeout,
        )
        self.paster = paster or Paster(config.paste_settle)
        self.sound = sound or Player(config.sound_volume)
        self.listener = ChordListener(self.chord, self._on_engage, self._on_disengage)

        self._lock = threading.Lock()
        self._recording = False
        self._busy = False  # a transcription is in flight

    # -- lifecycle ---------------------------------------------------------

    def run(self) -> None:
        log.info(
            "hotkey %s · model %s · language %s",
            self.chord,
            self.config.model,
            self.config.language or "auto",
        )
        if not accessibility_trusted():
            log.warning("Accessibility is not granted — transcripts will be left on")
            log.warning("the clipboard instead of pasted. %s", ACCESSIBILITY_HINT)
        log.info("hold the hotkey to dictate; Ctrl+C to quit")
        self.listener.start()
        try:
            while self.listener.running:
                self.listener.join(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            self.close()

    def close(self) -> None:
        self.listener.stop()
        if self.recorder.active:
            self.recorder.stop()
        log.info("bye")

    # -- chord callbacks ---------------------------------------------------

    def _on_engage(self) -> None:
        with self._lock:
            if self._recording or self._busy:
                return
            self._recording = True
        # Cue first, so the mic opens into as little of it as possible.
        self.sound.start()
        log.info("recording…")
        self.recorder.start()

    def _on_disengage(self) -> None:
        with self._lock:
            if not self._recording:
                return
            self._recording = False
            self._busy = True

        clip = self.recorder.stop()
        self.sound.stop()
        if clip.duration < self.config.min_seconds:
            log.info("discarded %.2fs tap", clip.duration)
            with self._lock:
                self._busy = False
            return
        threading.Thread(target=self._handle, args=(clip,), daemon=True).start()

    # -- worker ------------------------------------------------------------

    def _handle(self, clip: Clip) -> None:
        try:
            transcript = self.client.transcribe(clip)
            if not transcript.text:
                log.warning("empty transcript (%.1fs clip)", clip.duration)
                self.sound.error()
                return
            self.paster.paste(transcript.text, before=self.listener.wait_until_released)
            cost = f" · ${transcript.cost:.5f}" if transcript.cost is not None else ""
            log.info(
                '%.1fs → %.1fs%s · "%s"',
                clip.duration,
                transcript.latency,
                cost,
                transcript.text,
            )
        except ClientError as exc:
            log.error("transcription failed: %s", exc)
            self.sound.error()
        except Exception:  # never let a worker take the process down
            log.exception("unexpected failure while handling a clip")
            self.sound.error()
        finally:
            with self._lock:
                self._busy = False
