"""The contract every transcription backend implements."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import numpy as np

from talkie.audio import Clip


@dataclass(frozen=True)
class Transcript:
    """What came back for one clip."""

    text: str
    model: str
    latency: float
    usage: dict[str, Any] = field(default_factory=dict)

    @property
    def cost(self) -> float | None:
        """USD for this request, when the backend reports it."""
        cost = self.usage.get("cost")
        return cost if isinstance(cost, (int, float)) else None

    def __bool__(self) -> bool:
        return bool(self.text)


@dataclass(frozen=True)
class KeyInfo:
    """What a credential preflight managed to learn.

    Deliberately thin. OpenRouter will name the key and quote a balance;
    OpenAI will confirm the key works and say nothing else. Only `label` is
    promised, so `--check` can print the same line for any provider.
    """

    label: str
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.label} · {self.detail}" if self.detail else self.label


@runtime_checkable
class TranscriptionClient(Protocol):
    """Swap in any backend that can turn a clip into a transcript."""

    def transcribe(self, clip: Clip) -> Transcript:
        """Return the transcript, or raise a ClientError."""
        ...

    def check(self) -> KeyInfo:
        """Confirm the credential works, or raise a ClientError."""
        ...


@runtime_checkable
class StreamingSession(Protocol):
    """One dictation's worth of live transcription.

    Opened on press and closed on release — never reused, and never the same
    session as a one-shot request for the same clip (SPEC §6.4).
    """

    def feed(self, block: np.ndarray) -> None:
        """Hand over a block of mic audio. Called from the audio thread, so
        this must not block and must not raise."""
        ...

    def finish(self) -> Transcript:
        """Close the input, wait for the final text, or raise a ClientError."""
        ...

    def cancel(self) -> None:
        """Abandon the session. Never raises; there is nothing left to save."""
        ...


@runtime_checkable
class StreamingClient(Protocol):
    """A backend that can transcribe while the audio is still arriving."""

    def open(self, on_partial: Callable[[str], None] | None = None) -> StreamingSession:
        """Start a session. Returns before the socket is up, so the mic can
        open into the connect rather than after it."""
        ...

    def check(self) -> KeyInfo:
        """Confirm the credential works, or raise a ClientError."""
        ...
