"""OpenAI transcription client — one-shot today, realtime next.

Talkie reaches OpenAI directly rather than through OpenRouter because OpenRouter
cannot stream (SPEC §6.2). This module is the one-shot half of that: it posts a
finished clip to `/v1/audio/transcriptions` and behaves exactly like the
OpenRouter client. The realtime session lands beside it.

Two things differ from OpenRouter and shape the code:

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

import logging
import time
from typing import Any

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

    # -- cost --------------------------------------------------------------

    def _usage(self, result: Any, clip: Clip) -> dict[str, Any]:
        """What the response reported, plus the price it did not.

        `cost` lands in the same key OpenRouter's reported price uses, so
        history and the settings page need no per-provider special case. It is
        absent — not zero — for a model priced per token.
        """
        usage = _reported(result)
        cost = providers.price(providers.OPENAI, self.model, _seconds(result, usage, clip))
        if cost is not None:
            usage = {**usage, "cost": cost}
        return usage


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


def _seconds(result: Any, usage: dict[str, Any], clip: Clip) -> float:
    """How much audio to bill for.

    The response's own duration wins where there is one, because that is what
    OpenAI billed. The recorded length is the fallback, and it is close enough:
    it differs from the server's figure only by what the WAV header costs.
    """
    for candidate in (usage.get("seconds"), usage.get("duration"),
                      getattr(result, "duration", None)):
        if isinstance(candidate, (int, float)) and candidate > 0:
            return float(candidate)
    return clip.duration


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
    if isinstance(exc, openai.APIStatusError):
        if status is not None and status >= 500:
            return ServerError(message, status)
        return ClientError(message, status)
    return ClientError(f"transcription failed: {message}")
