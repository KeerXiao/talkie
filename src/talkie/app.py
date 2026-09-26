"""Wires capture, transcription and pasting into a push-to-talk loop."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime, timezone

from talkie.audio import Clip, Recorder, blocks, samples
from talkie.client import ClientError, StreamingClient, TranscriptionClient
from talkie.client.base import StreamingSession, Transcript
from talkie.client.factory import for_config, streaming_for_config
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
        on_partial: Callable[[str], None] | None = None,
        client_factory: Callable[[Config], TranscriptionClient] | None = None,
        stream_factory: Callable[[Config], StreamingClient] | None = None,
    ) -> None:
        self.config = config
        self.chord = parse_hotkey(config.hotkey)
        self.recorder = recorder or Recorder(config.sample_rate, config.channels)
        # A client is bound to one model and language, so a settings change
        # builds a new one rather than mutating this one (DESIGN 2). The
        # factory is the seam that lets apply() do that with a fake, too.
        self._new_client = client_factory or for_config
        self._new_stream = stream_factory or streaming_for_config
        # Exactly one of these, chosen by the running mode. Holding both would
        # make a cross-mode fallback possible, and there is deliberately no
        # such thing (SPEC §6.4).
        self.stream_client = self._new_stream(config) if config.streaming else None
        self.client = client or (None if config.streaming else self._new_client(config))
        self.paster = paster or Paster(config.paste_settle)
        self.sound = sound or Player(config.sound_volume)
        self.history = history
        self.listener = ChordListener(self.chord, self._on_engage, self._on_disengage)

        # Observers are optional: the terminal build sets none of them.
        self._on_state = on_state
        self._on_record = on_record
        self._on_partial = on_partial

        self._lock = threading.Lock()
        self._recording = False
        self._busy = False  # a transcription is in flight
        self._started_at: datetime | None = None
        self._session: StreamingSession | None = None
        self._streamed = False  # how the clip now being held will be sent

    def _emit(self, state: str) -> None:
        """Tell the UI, if there is one. A broken observer must not stop dictation."""
        if self._on_state is None:
            return
        try:
            self._on_state(state)
        except Exception:
            log.exception("state observer failed")

    def _emit_partial(self, text: str) -> None:
        """Live text, straight off the socket thread. PR 4 puts it on screen."""
        log.debug("partial: %s", text)
        if self._on_partial is None:
            return
        try:
            self._on_partial(text)
        except Exception:
            log.exception("partial observer failed")

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

        Both clients are built before anything is rebound, so a config the
        factory rejects leaves the app running exactly as it was rather than
        half-applied.
        """
        stream = self._new_stream(config) if config.streaming else None
        client = None if config.streaming else self._new_client(config)
        self.config = config
        self.client = client
        self.stream_client = stream
        self.sound.volume = config.sound_volume
        if self.history is not None:
            self.history.keep = config.history_keep
            self.history.prune()
        log.info(
            "settings applied · %s %s · %s · language %s",
            config.provider,
            config.model,
            config.running_mode,
            config.language or "auto",
        )

    # -- lifecycle ---------------------------------------------------------

    def run(self) -> None:
        log.info(
            "hotkey %s · %s %s · %s · language %s",
            self.chord,
            self.config.provider,
            self.config.model,
            self.config.running_mode,
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
        with self._lock:
            session, self._session = self._session, None
        if session is not None:
            session.cancel()
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
        # open() returns before the socket is up, so connecting overlaps the
        # first moments of speech instead of delaying the mic.
        self._streamed = self.stream_client is not None
        session = self._open_session()
        with self._lock:
            self._session = session
        self.recorder.start(on_frame=session.feed if session else None)

    def _open_session(self) -> StreamingSession | None:
        """A live session for this hold, or None when this run is one-shot.

        A session that cannot be opened leaves `_streamed` set and the session
        None, which fails the clip. It is never a reason to send the same audio
        the other way: the two modes are never combined (§6.4).
        """
        if self.stream_client is None:
            return None
        try:
            return self.stream_client.open(on_partial=self._emit_partial)
        except Exception:
            log.exception("could not open a live session")
            return None

    def _on_disengage(self) -> None:
        with self._lock:
            if not self._recording:
                return
            self._recording = False
            self._busy = True

        clip = self.recorder.stop()
        with self._lock:
            session, self._session = self._session, None
            streamed, self._streamed = self._streamed, False
            # Captured here, not read on the worker: a settings save can null
            # `client` (switching to a streaming model) between release and the
            # request, and the clip must finish against what it started with.
            client = self.client
        if clip.duration < self.config.min_seconds:
            # Nothing is sent, so this is the whole outcome: cue it here.
            self.sound.stop()
            log.info("discarded %.2fs tap", clip.duration)
            if session is not None:
                session.cancel()
            with self._lock:
                self._busy = False
            self._emit(IDLE)
            return
        self._emit(TRANSCRIBING)
        threading.Thread(
            target=self._handle, args=(clip, session, streamed, client), daemon=True
        ).start()

    # -- worker ------------------------------------------------------------

    # One cue per dictation, and the outcome picks it. The release used to cue
    # immediately and a failure cued again about half a second later, which on a
    # sub-second tap arrived as a stutter rather than as two distinct sounds —
    # and the second one is the only one carrying news. Between release and the
    # result the overlay says "Transcribing…" and the menu bar shows ⏳, so the
    # silence is not the only feedback.
    def _handle(
        self,
        clip: Clip,
        session: StreamingSession | None = None,
        streamed: bool = False,
        client: TranscriptionClient | None = None,
    ) -> None:
        started_at = self._started_at
        try:
            transcript = self._transcribe(clip, session, streamed, client or self.client)
            if not transcript.text:
                # Error only — see _cue_once below.
                log.warning("empty transcript (%.1fs clip)", clip.duration)
                self.sound.error()
                self._save(clip, started_at, error="empty transcript")
                self._emit(ERROR)
                return
            self.sound.stop()
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
            if session is not None:
                session.cancel()  # a no-op once finish() has returned
            with self._lock:
                self._busy = False

    def _transcribe(
        self,
        clip: Clip,
        session: StreamingSession | None,
        streamed: bool,
        client: TranscriptionClient | None,
    ) -> Transcript:
        """The clip's one and only trip to a backend.

        A live session that failed — even one that never opened — is a failed
        clip. Retrying it as a one-shot request would double the bill, paste a
        transcript the user watched fail, and is explicitly out of scope
        (§6.4), so this raises rather than reaching for the other client.
        """
        if streamed:
            if session is None:
                raise ClientError("could not open a live session for this clip")
            return session.finish()
        if client is None:
            raise ClientError("no transcription client for this clip")
        return client.transcribe(clip)

    # -- retry -------------------------------------------------------------

    def transcribe_again(self, clip: Clip) -> Transcript:
        """Send an already-recorded clip once more.

        For a failed dictation whose audio history kept: the user should not
        have to say it twice. It goes through whatever is configured *now*,
        because the usual reason a clip failed is that something was wrong with
        the provider, and the point of retrying is that it has since changed.

        Nothing is pasted. The history window has focus when this is called, so
        a paste would land in talkie itself — the same reason the overlay never
        takes focus (DESIGN 4.1).
        """
        if self.stream_client is None:
            return self.client.transcribe(clip)
        # A stream-only model has no file endpoint, so the stored clip is fed
        # through a session exactly as the microphone would have fed it — at
        # its own recorded rate, not the mic's current one.
        frames, rate = samples(clip.wav)
        session = self.stream_client.open(sample_rate=rate)
        try:
            for block in blocks(frames):
                session.feed(block)
            return session.finish()
        finally:
            session.cancel()  # a no-op once finish() has returned

    def _save(self, clip, started_at, transcript=None, error=None) -> None:
        """Persist the interaction, if this build keeps history."""
        if self.history is None:
            return
        self._emit_record(
            self.history.record(
                clip, transcript=transcript, error=error, started_at=started_at
            )
        )
