"""The contract every transcription backend implements."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

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


@runtime_checkable
class TranscriptionClient(Protocol):
    """Swap in any backend that can turn a clip into a transcript."""

    def transcribe(self, clip: Clip) -> Transcript:
        """Return the transcript, or raise a ClientError."""
        ...
