"""On-disk record of every interaction, success or failure.

The history window reads this directory, so the JSON shape here is a contract
rather than an implementation detail. Writes are atomic and the WAV lands
before its JSON, so a visible entry always has playable audio behind it.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from talkie.audio import Clip
from talkie.client import Transcript

log = logging.getLogger(__name__)

DEFAULT_ROOT = Path.home() / ".talkie" / "history"
DEFAULT_KEEP = 50
STAMP = "%Y%m%dT%H%M%SZ"
# Ids are generated, but they also arrive from the UI; never trust them as paths.
ID_RE = re.compile(r"^\d{8}T\d{6}Z(-\d+)?$")


@dataclass(frozen=True)
class Interaction:
    """One hold-talk-release, whether or not it produced text."""

    id: str
    started_at: datetime
    duration: float
    model: str
    text: str = ""
    latency: float | None = None
    cost: float | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "started_at": self.started_at.isoformat(),
            "duration": round(self.duration, 3),
            "model": self.model,
            "text": self.text,
            "latency": self.latency,
            "cost": self.cost,
            "error": self.error,
        }

    @classmethod
    def from_json(cls, data: dict) -> Interaction:
        return cls(
            id=data["id"],
            started_at=datetime.fromisoformat(data["started_at"]),
            duration=float(data.get("duration", 0.0)),
            model=data.get("model", ""),
            text=data.get("text", ""),
            latency=data.get("latency"),
            cost=data.get("cost"),
            error=data.get("error"),
        )


class History:
    """A capped, newest-first directory of interactions."""

    def __init__(self, root: Path | None = None, keep: int = DEFAULT_KEEP) -> None:
        self.root = Path(root) if root else DEFAULT_ROOT
        self.keep = keep

    # -- writing -----------------------------------------------------------

    def record(
        self,
        clip: Clip,
        transcript: Transcript | None = None,
        error: str | None = None,
        started_at: datetime | None = None,
    ) -> Interaction | None:
        """Persist one interaction. Never raises — storage must not break dictation."""
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            when = started_at or datetime.now(timezone.utc)
            entry = Interaction(
                id=self._free_id(when),
                started_at=when,
                duration=clip.duration,
                model=transcript.model if transcript else "",
                text=transcript.text if transcript else "",
                latency=transcript.latency if transcript else None,
                cost=transcript.cost if transcript else None,
                error=error,
            )
            # Audio first: a readable JSON must never point at a missing WAV.
            if clip.wav:
                self._atomic_write(self.root / f"{entry.id}.wav", clip.wav)
            self._atomic_write(
                self.root / f"{entry.id}.json",
                json.dumps(entry.to_json(), indent=2).encode(),
            )
            self.prune()
            return entry
        except Exception:
            log.exception("could not write history (continuing anyway)")
            return None

    def _free_id(self, when: datetime) -> str:
        base = when.strftime(STAMP)
        if not (self.root / f"{base}.json").exists():
            return base
        # Two clips inside one second; keep both rather than overwrite.
        for n in range(1, 1000):
            if not (self.root / f"{base}-{n}.json").exists():
                return f"{base}-{n}"
        return f"{base}-{os.getpid()}"

    @staticmethod
    def _atomic_write(path: Path, payload: bytes) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(payload)
        os.replace(tmp, path)

    # -- reading -----------------------------------------------------------

    def list(self) -> list[Interaction]:
        """Newest first. Unreadable entries are skipped, not fatal."""
        entries = []
        for path in sorted(self.root.glob("*.json"), reverse=True):
            try:
                entries.append(Interaction.from_json(json.loads(path.read_text())))
            except Exception:
                log.warning("skipping unreadable history entry %s", path.name)
        return entries

    def get(self, clip_id: str) -> Interaction | None:
        path = self._json(clip_id)
        if path is None or not path.exists():
            return None
        try:
            return Interaction.from_json(json.loads(path.read_text()))
        except Exception:
            return None

    def audio(self, clip_id: str) -> bytes | None:
        path = self._wav(clip_id)
        return path.read_bytes() if path and path.exists() else None

    # -- mutating ----------------------------------------------------------

    def delete(self, clip_id: str) -> bool:
        removed = False
        for path in (self._json(clip_id), self._wav(clip_id)):
            if path and path.exists():
                path.unlink()
                removed = True
        return removed

    def clear(self) -> int:
        count = 0
        for path in sorted(self.root.glob("*.json")):
            self.delete(path.stem)
            count += 1
        return count

    def prune(self) -> int:
        """Drop everything past `keep`, oldest first."""
        stems = sorted((p.stem for p in self.root.glob("*.json")), reverse=True)
        dropped = 0
        for stem in stems[self.keep :]:
            self.delete(stem)
            dropped += 1
        return dropped

    # -- paths -------------------------------------------------------------

    def _json(self, clip_id: str) -> Path | None:
        return self.root / f"{clip_id}.json" if ID_RE.match(clip_id or "") else None

    def _wav(self, clip_id: str) -> Path | None:
        return self.root / f"{clip_id}.wav" if ID_RE.match(clip_id or "") else None
