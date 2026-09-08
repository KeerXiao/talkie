"""Wires capture, transcription and pasting into a push-to-talk loop."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime, timezone

from talkie.audio import Clip, Recorder
from talkie.client import ClientError, OpenRouterClient, TranscriptionClient
from talkie.config import Config
from talkie.history import History, Interaction
from talkie.hotkey import ChordListener, parse_hotkey
from talkie.paste import Paster
from talkie.permissions import ACCESSIBILITY_HINT, accessibility_trusted
from talkie.sound import Player

log = logging.getLogger(__name__)

# What the menu-bar icon reflects. Strings, not an enum, because they cross
# into the UI layer and get rendered directly.
IDLE = "idle"
RECORDING = "recording"
TRANSCRIBING = "transcribing"
ERROR = "error"


def _openrouter(config: Config) -> TranscriptionClient:
    return OpenRouterClient(
        api_key=config.api_key,
        model=config.model,
        language=config.language,
        timeout=config.request_timeout,
    )


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
        history: History | None = None,
        on_state: Callable[[str], None] | None = None,
        on_record: Callable[[Interaction], None] | None = None,
        client_factory: Callable[[Config], TranscriptionClient] | None = None,
    ) -> None:
        self.config = config
        self.chord = parse_hotkey(config.hotkey)
        self.recorder = recorder or Recorder(config.sample_rate, config.channels)
        # A client is bound to one model and language, so a settings change
        # builds a new one rather than mutating this one (DESIGN 2). The
        # factory is the seam that lets apply() do that with a fake, too.
        self._new_client = client_factory or _openrouter
        self.client = client or self._new_client(config)
        self.paster = paster or Paster(config.paste_settle)
        self.sound = sound or Player(config.sound_volume)
        self.history = history
        self.listener = ChordListener(self.chord, self._on_engage, self._on_disengage)

        # Observers are optional: the terminal build sets neither.
        self._on_state = on_state
        self._on_record = on_record

        self._lock = threading.Lock()
        self._recording = False
        self._busy = False  # a transcription is in flight
        self._started_at: datetime | None = None

    def _emit(self, state: str) -> None:
        """Tell the UI, if there is one. A broken observer must not stop dictation."""
        if self._on_state is None:
            return
        try:
            self._on_state(state)
        except Exception:
            log.exception("state observer failed")

    def _emit_record(self, entry: Interaction | None) -> None:
        if entry is None or self._on_record is None:
            return
        try:
            self._on_record(entry)
        except Exception:
            log.exception("record observer failed")

    # -- settings ----------------------------------------------------------

    def apply(self, config: Config) -> None:
        """Adopt changed settings without restarting the process.

        Called from the UI's bridge thread while dictation may be in flight, so
        it only rebinds attributes — a clip already being transcribed finishes
        against the client it started with, which is what you want: its history
        row should name the model that actually did the work.

        The hotkey is not read here. Changing it means tearing down the pynput
        listener, and nothing in the UI offers that yet.
        """
        self.config = config
        self.client = self._new_client(config)
        self.sound.volume = config.sound_volume
        if self.history is not None:
            self.history.keep = config.history_keep
            self.history.prune()
        log.info(
            "settings applied · model %s · language %s",
            config.model,
            config.language or "auto",
        )

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
            self._started_at = datetime.now(timezone.utc)
        # Cue first, so the mic opens into as little of it as possible.
        self.sound.start()
        self._emit(RECORDING)
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
            self._emit(IDLE)
            return
        self._emit(TRANSCRIBING)
        threading.Thread(target=self._handle, args=(clip,), daemon=True).start()

    # -- worker ------------------------------------------------------------

    def _handle(self, clip: Clip) -> None:
        started_at = self._started_at
        try:
            transcript = self.client.transcribe(clip)
            if not transcript.text:
                log.warning("empty transcript (%.1fs clip)", clip.duration)
                self.sound.error()
                self._save(clip, started_at, error="empty transcript")
                self._emit(ERROR)
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
            self._save(clip, started_at, transcript=transcript)
            self._emit(IDLE)
        except ClientError as exc:
            log.error("transcription failed: %s", exc)
            self.sound.error()
            self._save(clip, started_at, error=str(exc))
            self._emit(ERROR)
        except Exception:  # never let a worker take the process down
            log.exception("unexpected failure while handling a clip")
            self.sound.error()
            self._save(clip, started_at, error="unexpected failure")
            self._emit(ERROR)
        finally:
            with self._lock:
                self._busy = False

    def _save(self, clip, started_at, transcript=None, error=None) -> None:
        """Persist the interaction, if this build keeps history."""
        if self.history is None:
            return
        self._emit_record(
            self.history.record(
                clip, transcript=transcript, error=error, started_at=started_at
            )
        )
