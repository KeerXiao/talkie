"""OpenAI transcription clients — one batch, one realtime.

Talkie reaches OpenAI directly rather than through OpenRouter because OpenRouter
cannot stream (SPEC §6.2). `OpenAIClient` posts a finished clip to
`/v1/audio/transcriptions` and behaves exactly like the OpenRouter client.
`OpenAIStreamingClient` opens a realtime WebSocket per hold of the hotkey and
emits text while the key is still down, which is why this module exists.

Three things about the realtime session are not negotiable and shape the code:

  - **24 kHz PCM only.** talkie records at 16 kHz, so every block is resampled
    on the way out. The clip written to history is the untouched 16 kHz
    recording — resampling is a wire concern, not a recording one.
  - **No turn detection.** Push-to-talk already knows where the turn ends, so
    VAD is off and the buffer is committed by hand on release.
  - **The audio callback must never block.** Blocks are queued and a pump
    thread does the sending, because stalling PortAudio would drop the
    recording itself.

Two more differ from OpenRouter across both clients:

  - **Nothing here is priced by the response.** OpenAI bills per minute of
    audio and never quotes a figure, so the cost in the history window is
    arithmetic against the table in `talkie.providers`. A model priced per
    token carries no price there and its clips show no cost at all, which is
    better than a number nobody can check.
  - **The SDK raises, it does not return statuses.** `_error_for` maps its
    exception hierarchy onto talkie's, so callers above this layer still see
    only `ClientError` and its subclasses.

Absolute imports mean `import openai` here reaches the SDK, not this file. It
is imported lazily so an OpenRouter-only run never pays for the import, and so
a broken install is reported as a `ClientError` rather than an ImportError at
startup.
"""

from __future__ import annotations

import base64
import logging
import queue
import threading
import time
from collections.abc import Callable
from typing import Any

import numpy as np

from talkie import providers
from talkie.audio import Clip
from talkie.client.base import KeyInfo, Transcript
from talkie.client.errors import (
    AuthError,
    ClientError,
    InsufficientCreditsError,
    NetworkError,
    RateLimitError,
    ResponseError,
    ServerError,
)

log = logging.getLogger(__name__)

# The realtime input format. Not a default — the API accepts nothing else.
TARGET_RATE = 24000

DELTA = "conversation.item.input_audio_transcription.delta"
COMPLETED = "conversation.item.input_audio_transcription.completed"
FAILED = "conversation.item.input_audio_transcription.failed"
COMMITTED = "input_audio_buffer.committed"

# Ends the pump loop. A sentinel rather than a flag so the pump blocks on the
# queue and wakes the instant the key is released.
_EOF = object()


def _sdk():
    """The `openai` package, or a ClientError explaining how to get it."""
    try:
        import openai
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise ClientError(
            "the openai package is not installed — run `make install`"
        ) from exc
    return openai


class OpenAIClient:
    """Transcribes finished clips with one OpenAI model.

    The model is bound at construction, as with OpenRouter: a run uses one
    model and swapping is a new client. Retries are left to the SDK, which
    already backs off on the failures worth retrying.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        language: str | None = "en",
        timeout: float = 30.0,
        client: Any | None = None,
        max_retries: int = 2,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.language = language
        self.timeout = timeout
        self._client = client or _sdk().OpenAI(
            api_key=api_key, timeout=timeout, max_retries=max_retries
        )

    def transcribe(self, clip: Clip) -> Transcript:
        started = time.monotonic()
        kwargs: dict[str, Any] = {
            "model": self.model,
            # A tuple, not a file object: the clip only ever exists in memory,
            # and the SDK needs a name to infer the part's content type.
            "file": ("clip.wav", clip.wav, "audio/wav"),
        }
        if self.language:
            kwargs["language"] = self.language
        try:
            result = self._client.audio.transcriptions.create(**kwargs)
        except Exception as exc:
            raise _error_for(exc) from exc

        text = getattr(result, "text", None)
        if text is None:
            raise ResponseError(f"unexpected response: {str(result)[:300]}")
        return Transcript(
            text=text.strip(),
            model=self.model,
            latency=time.monotonic() - started,
            usage=self._usage(result, clip),
        )

    def check(self) -> KeyInfo:
        """Credential preflight.

        OpenAI publishes no balance endpoint, so this is a ping: listing models
        is the cheapest call that fails the same way a transcription would when
        the key is wrong.
        """
        try:
            models = self._client.models.list()
        except Exception as exc:
            raise _error_for(exc) from exc
        count = len(getattr(models, "data", None) or [])
        # OpenAI does not name keys the way OpenRouter does, so there is no
        # label to report — only that the credential reached the API.
        return KeyInfo("(unlabelled)", f"{count} models visible" if count else "reachable")

    def _usage(self, result: Any, clip: Clip) -> dict[str, Any]:
        usage = _reported(result)
        return _priced(self.model, usage, _seconds(result, usage, clip.duration))


def _reported(result: Any) -> dict[str, Any]:
    """The SDK's usage object as plain JSON-able data."""
    usage = getattr(result, "usage", None)
    if usage is None:
        return {}
    for attr in ("model_dump", "to_dict", "dict"):
        dump = getattr(usage, attr, None)
        if callable(dump):
            try:
                return dump()
            except Exception:  # pragma: no cover - defensive against SDK churn
                break
    return dict(usage) if isinstance(usage, dict) else {}


def _seconds(result: Any, usage: dict[str, Any], fallback: float) -> float:
    """How much audio to bill for.

    The response's own duration wins where there is one, because that is what
    OpenAI billed. The audio talkie handled is the fallback: the whole hold of
    the hotkey, including the silence before the first word, so it errs high
    rather than low. `whisper-1` reports no usage at all, so that fallback is
    the only figure its clips ever get.
    """
    for candidate in (usage.get("seconds"), usage.get("duration"),
                      getattr(result, "duration", None)):
        if isinstance(candidate, (int, float)) and candidate > 0:
            return float(candidate)
    return fallback


def _priced(model: str, usage: dict[str, Any], seconds: float) -> dict[str, Any]:
    """The reported usage, plus the price the response did not quote.

    `cost` lands in the same key OpenRouter's reported price uses, so history
    and the settings page need no per-provider special case. It is absent —
    not zero — for a model priced per token.
    """
    cost = providers.price(providers.OPENAI, model, seconds)
    return {**usage, "cost": cost} if cost is not None else usage


def _error_for(exc: Exception) -> ClientError:
    """Map an SDK exception onto talkie's failure vocabulary."""
    if isinstance(exc, ClientError):
        return exc
    openai = _sdk()
    status = getattr(exc, "status_code", None)
    message = str(getattr(exc, "message", None) or exc)
    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return AuthError(message, status)
    if isinstance(exc, openai.RateLimitError):
        # OpenAI bills a spent quota as a 429, not a 402 like OpenRouter does.
        if getattr(exc, "code", None) == "insufficient_quota":
            return InsufficientCreditsError(message, status)
        return RateLimitError(message, status)
    if isinstance(exc, (openai.APIConnectionError, openai.APITimeoutError)):
        return NetworkError(message)
    if isinstance(exc, openai.InternalServerError):
        return ServerError(message, status)
    # A 200 whose body the SDK could not parse — the same thing ResponseError
    # means, and not an APIStatusError, so it would otherwise fall through.
    if isinstance(exc, openai.APIResponseValidationError):
        return ResponseError(message, getattr(exc, "status_code", None))
    if isinstance(exc, openai.APIStatusError):
        if status is not None and status >= 500:
            return ServerError(message, status)
        return ClientError(message, status)
    return ClientError(f"transcription failed: {message}")


class _Resampler:
    """16 kHz mono int16 → 24 kHz, one block at a time.

    Linear interpolation, which is plenty for speech at a 1.5× ratio and costs
    nothing. The read position and the previous block's last sample are carried
    across calls: resampling each block independently would put a discontinuity
    at every boundary, ~30 times a second, which a transcription model hears as
    a click.
    """

    def __init__(self, source_rate: int, target_rate: int = TARGET_RATE) -> None:
        self.step = source_rate / target_rate
        self._pos = 0.0
        self._tail: float | None = None

    def __call__(self, block: np.ndarray) -> np.ndarray:
        block = np.asarray(block).reshape(-1).astype(np.float32)
        if block.size == 0:
            return np.empty(0, dtype=np.int16)
        if self.step == 1.0:
            return block.astype(np.int16)

        # Index 0 of `samples` is the previous block's last sample, so `_pos`
        # is measured from there and the interpolation spans the seam.
        if self._tail is not None:
            samples = np.concatenate(([self._tail], block))
        else:
            samples = block
        last = samples.size - 1
        if last < 1:
            self._tail = float(samples[-1])
            return np.empty(0, dtype=np.int16)

        positions = np.arange(self._pos, last, self.step)
        out = np.interp(positions, np.arange(samples.size), samples)
        self._pos = (positions[-1] + self.step if positions.size else self._pos) - last
        self._tail = float(samples[-1])
        return np.clip(np.rint(out), -32768, 32767).astype(np.int16)


class OpenAIStreamingClient:
    """Opens a realtime transcription session per hold of the hotkey.

    One session per dictation rather than one long-lived socket: a push-to-talk
    clip is seconds long, an idle socket is a reconnect problem waiting to
    happen, and connecting overlaps the first half-second of speech anyway
    because `open()` returns before the socket is up.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        language: str | None = "en",
        sample_rate: int = 16000,
        delay: str | None = None,
        noise_reduction: str | None = "near_field",
        connect_timeout: float = 10.0,
        final_timeout: float = 20.0,
        client: Any | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.language = language
        self.sample_rate = sample_rate
        self.delay = delay
        self.noise_reduction = noise_reduction
        self.connect_timeout = connect_timeout
        self.final_timeout = final_timeout
        self._client = client or _sdk().OpenAI(api_key=api_key)

    def open(
        self,
        on_partial: Callable[[str], None] | None = None,
        sample_rate: int | None = None,
    ) -> _Session:
        """`sample_rate` overrides the configured one for this session only —
        a stored clip carries its own rate in its header, and resampling from
        the wrong one would send the model slowed-down or hurried speech."""
        rate = sample_rate or self.sample_rate
        session = _Session(
            # `intent=transcription`, not `model=`: the query parameter names a
            # realtime *session* model, and a transcription session has none —
            # its model goes in session.audio.input.transcription instead.
            # Passing it here is rejected as invalid_model, mid-clip.
            connect=lambda: self._client.realtime.connect(
                extra_query={"intent": "transcription"}
            ),
            config=self.session_config(),
            resample=_Resampler(rate),
            model=self.model,
            sample_rate=rate,
            on_partial=on_partial,
            connect_timeout=self.connect_timeout,
            final_timeout=self.final_timeout,
        )
        session.start()
        return session

    def check(self) -> KeyInfo:
        """Same preflight as the one-shot client — it is the same credential."""
        return OpenAIClient(
            self.api_key, self.model, self.language, client=self._client
        ).check()

    def session_config(self) -> dict[str, Any]:
        """The `session.update` payload.

        `turn_detection` is explicitly null: the hotkey is the turn detector,
        and server VAD would cut the transcript at a pause mid-sentence.
        """
        transcription: dict[str, Any] = {"model": self.model}
        if self.language:
            transcription["language"] = self.language
        if self.delay:
            transcription["delay"] = self.delay
        audio_input: dict[str, Any] = {
            "format": {"type": "audio/pcm", "rate": TARGET_RATE},
            "transcription": transcription,
            "turn_detection": None,
        }
        if self.noise_reduction:
            audio_input["noise_reduction"] = {"type": self.noise_reduction}
        return {"type": "transcription", "audio": {"input": audio_input}}


class _Session:
    """One socket, two threads, one dictation.

    The reader thread owns the connection: it connects, configures, then blocks
    on incoming events for the rest of the session. The pump thread owns the
    outgoing audio, because the reader is blocked and the audio callback must
    never be. `feed()` therefore only ever touches a queue.
    """

    def __init__(
        self,
        connect: Callable[[], Any],
        config: dict[str, Any],
        resample: Callable[[np.ndarray], np.ndarray],
        model: str,
        sample_rate: int,
        on_partial: Callable[[str], None] | None = None,
        connect_timeout: float = 10.0,
        final_timeout: float = 20.0,
    ) -> None:
        self._connect = connect
        self._config = config
        self._resample = resample
        self._model = model
        self._sample_rate = sample_rate
        self._on_partial = on_partial
        self._connect_timeout = connect_timeout
        self._final_timeout = final_timeout

        self._audio: queue.Queue[Any] = queue.Queue()
        self._ready = threading.Event()   # the socket is configured
        self._done = threading.Event()    # the final transcript, or a failure
        self._flushed = threading.Event() # every queued block has been sent
        # `_connection` is written by the reader once the handshake returns
        # and read by the pump and by _close(), so it travels under the lock.
        # `_closing` is how a close that arrives *during* the handshake is
        # honoured: the reader checks it the moment the socket exists.
        self._connection: Any = None
        self._closing = False
        self._error: ClientError | None = None
        self._cancelled = False

        self._lock = threading.Lock()
        self._items: dict[str, str] = {}
        self._order: list[str] = []
        self._final_item: str | None = None
        self._usage: dict[str, Any] = {}
        self._samples = 0
        # Latency is measured from the release, not from `open()`: a streamed
        # clip's `open()` happens before the user has spoken, so timing from
        # there would report the length of the dictation, not the wait after
        # it, and history rows would not be comparable with one-shot ones.
        self._releasing = 0.0
        self._reader: threading.Thread | None = None
        self._pump: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        self._reader = threading.Thread(target=self._read, daemon=True, name="talkie-rt-read")
        self._pump = threading.Thread(target=self._send, daemon=True, name="talkie-rt-send")
        self._reader.start()
        self._pump.start()

    def feed(self, block: np.ndarray) -> None:
        """Queue one block of mic audio. Runs on the audio thread."""
        if self._done.is_set() or self._cancelled:
            return
        self._samples += np.asarray(block).shape[0]
        self._audio.put(block)

    def finish(self) -> Transcript:
        """Commit what was said and wait for the final text.

        Both waits share one deadline. Budgeting them separately meant a hung
        connect cost `connect_timeout` *plus* `final_timeout` — 40 s with the
        shipped defaults — and `Talkie` stays busy for all of it, so the hotkey
        would be dead that whole time.
        """
        self._releasing = time.monotonic()
        deadline = self._releasing + self._final_timeout
        self._audio.put(_EOF)
        # The pump commits once the queue is empty; without that wait the
        # commit could reach the server before the last block of speech.
        self._flushed.wait(self._remaining(deadline))
        if not self._done.wait(self._remaining(deadline)):
            self._fail(NetworkError("the realtime session did not finish in time"))
        # Set even on the timeout path: it is what stops the reader waiting for
        # an event that is never coming.
        self._done.set()
        self._close()

        if self._error is not None:
            raise self._error
        if self._cancelled:
            # cancel() landed while this was waiting — Talkie.close() during a
            # dictation does exactly that. Returning the empty transcript would
            # tell the user they said nothing.
            raise ClientError("the live session was abandoned")
        return Transcript(
            text=self.text,
            model=self._model,
            latency=time.monotonic() - self._releasing,
            # The completed event reports the duration OpenAI billed; the
            # audio this session actually sent is the fallback.
            usage=_priced(
                self._model, self._usage, _seconds(None, self._usage, self.seconds)
            ),
        )

    def cancel(self) -> None:
        """Abandon the session. Safe to call twice, and after finish()."""
        self._cancelled = True
        self._audio.put(_EOF)
        self._done.set()
        self._close()

    @staticmethod
    def _remaining(deadline: float) -> float:
        return max(0.0, deadline - time.monotonic())

    # -- what has been heard so far ----------------------------------------

    @property
    def text(self) -> str:
        with self._lock:
            return " ".join(self._items[item] for item in self._order if self._items[item]).strip()

    @property
    def seconds(self) -> float:
        """Audio actually fed to this session, for pricing when the server
        reports no duration of its own."""
        return self._samples / self._sample_rate if self._sample_rate else 0.0

    # -- threads -----------------------------------------------------------

    def _send(self) -> None:
        """Drain the audio queue onto the socket, then commit."""
        if not self._ready.wait(self._connect_timeout):
            self._flushed.set()
            return
        # One snapshot, so a concurrent _close() cannot turn a send into an
        # AttributeError that reaches the user as the failure message.
        with self._lock:
            connection = self._connection
        if connection is None:
            self._flushed.set()
            return
        try:
            while True:
                block = self._audio.get()
                if block is _EOF:
                    break
                chunk = self._resample(block)
                if chunk.size:
                    connection.input_audio_buffer.append(
                        audio=base64.b64encode(chunk.tobytes()).decode()
                    )
            if not self._cancelled and self._samples:
                connection.input_audio_buffer.commit()
            elif not self._samples:
                # Committing an empty buffer is an error event, and a clip this
                # short is discarded upstream anyway.
                self._done.set()
        except Exception as exc:
            self._fail(_error_for(exc))
        finally:
            self._flushed.set()

    def _read(self) -> None:
        """Own the connection: configure it, then dispatch events until it ends.

        `connect()` returns a context manager whose `__enter__` performs the
        whole WebSocket handshake, so everything before the `with` body is time
        in which a cancel can arrive with no socket yet to close. That is the
        case `_closing` exists for: a tap shorter than `min_seconds` cancels
        well inside a handshake, and without this check the socket and this
        thread would both outlive the dictation.

        The SDK's reconnect is deliberately left off — `connect()` defaults
        `on_reconnecting` to None, so a dropped socket ends the iteration
        instead of silently opening a second one with default (VAD-on,
        wrong-format) session config.
        """
        try:
            with self._connect() as connection:
                with self._lock:
                    abandoned = self._closing
                    if not abandoned:
                        self._connection = connection
                if abandoned:
                    return  # __exit__ closes the socket on the way out
                connection.session.update(session=self._config)
                self._ready.set()
                for event in connection:
                    self._dispatch(event)
                    if self._done.is_set():
                        break
        except Exception as exc:
            self._fail(_error_for(exc))
        finally:
            self._ready.set()  # unblock the pump even when the connect failed
            if not self._done.is_set() and self._error is None:
                self._fail(NetworkError("the realtime session closed early"))
            self._done.set()

    def _dispatch(self, event: Any) -> None:
        kind = getattr(event, "type", None)
        if kind == DELTA:
            self._append(getattr(event, "item_id", ""), getattr(event, "delta", "") or "")
            self._emit()
        elif kind == COMPLETED:
            self._replace(
                getattr(event, "item_id", ""), (getattr(event, "transcript", "") or "").strip()
            )
            self._usage = _reported(event)
            self._emit()
            # With turn detection off there is one item per commit, so the
            # first completed transcript after the commit is the whole clip.
            self._done.set()
        elif kind == COMMITTED:
            self._final_item = getattr(event, "item_id", None)
        elif kind == FAILED:
            self._fail(ResponseError(_reason(event)))
            self._done.set()
        elif kind == "error":
            self._fail(_error_for_event(event))
            self._done.set()

    # -- helpers -----------------------------------------------------------

    def _append(self, item: str, delta: str) -> None:
        if not delta:
            return
        with self._lock:
            if item not in self._items:
                self._items[item] = ""
                self._order.append(item)
            self._items[item] += delta

    def _replace(self, item: str, text: str) -> None:
        with self._lock:
            if item not in self._items:
                self._order.append(item)
            self._items[item] = text

    def _emit(self) -> None:
        if self._on_partial is None:
            return
        try:
            self._on_partial(self.text)
        except Exception:
            log.exception("partial observer failed")

    def _fail(self, error: ClientError) -> None:
        if self._error is None and not self._cancelled:
            self._error = error

    def _close(self) -> None:
        """Close the socket, or tell the reader to when it finally has one."""
        with self._lock:
            self._closing = True
            connection = self._connection
            self._connection = None
        if connection is None:
            return
        try:
            connection.close()
        except Exception:  # the socket may already be gone; nothing to save
            log.debug("closing the realtime socket failed", exc_info=True)


def _reason(event: Any) -> str:
    """The message out of a failed/error event, whatever shape it arrived in."""
    error = getattr(event, "error", None)
    for attr in ("message", "code", "type"):
        value = getattr(error, attr, None) if error is not None else None
        if value:
            return str(value)
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or error)
    return str(getattr(event, "type", "realtime session failed"))


def _error_for_event(event: Any) -> ClientError:
    """An `error` event carries a code, not an exception, so it maps by hand."""
    error = getattr(event, "error", None)
    code = getattr(error, "code", None) or ""
    message = _reason(event)
    if "auth" in code or "invalid_api_key" in code:
        return AuthError(message)
    if "quota" in code or "insufficient" in code:
        return InsufficientCreditsError(message)
    if "rate_limit" in code:
        return RateLimitError(message)
    return ResponseError(message)
