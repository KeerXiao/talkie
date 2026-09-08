"""The surface the history window calls — this class *is* the UI's API.

`ui/src/bridge.d.ts` is the TypeScript view of it; the two are kept in step by
hand, so change both together. (DESIGN 5.)

Two rules follow from being on the far side of a JavaScript bridge:

  - every public method here is reachable from the page, so adding one widens
    what the window can do — keep the surface deliberate
  - every argument is untrusted, and every return value must be JSON

Deliberately free of any pywebview import: pywebview injects this object as
`window.pywebview.api`, but nothing here knows that, so the same class can be
served over loopback HTTP instead without touching its body.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Callable
from datetime import datetime, timezone

import pyperclip

from talkie.history import History, Interaction
from talkie.settings import (
    LANGUAGES,
    MAX_KEEP,
    MAX_VOLUME,
    MODELS,
    Settings,
    SettingsError,
    SettingsStore,
)

log = logging.getLogger(__name__)


class Api:
    """Read and mutate history and settings on behalf of the window."""

    def __init__(
        self,
        history: History,
        clipboard=pyperclip,
        settings: Settings | None = None,
        store: SettingsStore | None = None,
        on_settings_changed: Callable[[Settings], None] | None = None,
    ) -> None:
        self.history = history
        self.clipboard = clipboard
        self.settings = settings or Settings()
        self.store = store or SettingsStore()
        # The running app listens here to re-point the client and the menu bar.
        # Api itself knows nothing about either.
        self._on_settings_changed = on_settings_changed

    # -- reads -------------------------------------------------------------

    def list_history(self) -> list[dict]:
        """Newest first, without audio — clips are fetched only when played."""
        return [self._row(e) for e in self.history.list()]

    def clip_audio(self, clip_id: str) -> str | None:
        """A data URI for one clip, or None if the audio is gone.

        The bridge is JSON, so bytes cannot cross; this is why playback is
        lazy rather than bundled into list_history.
        """
        wav = self.history.audio(clip_id)
        if wav is None:
            return None
        return "data:audio/wav;base64," + base64.b64encode(wav).decode()

    def stats(self) -> dict:
        """Totals for today, in the user's local timezone."""
        start = datetime.now().astimezone().replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        today = [e for e in self.history.list() if e.started_at.astimezone() >= start]
        return {
            "clips": len(today),
            "seconds": round(sum(e.duration for e in today), 1),
            "cost": round(sum(e.cost or 0.0 for e in today), 6),
            "failures": sum(1 for e in today if not e.ok),
        }

    def get_settings(self) -> dict:
        """Current values, plus everything the page needs to render the form.

        The choices ship from here rather than being duplicated in TypeScript:
        the language list and the limits are settings.py's to define.
        """
        return {
            "settings": self.settings.to_json(),
            "languages": LANGUAGES,
            "models": MODELS,
            "limits": {"maxVolume": MAX_VOLUME, "maxKeep": MAX_KEEP},
        }

    # -- mutations ---------------------------------------------------------

    def update_settings(self, patch: dict) -> dict:
        """Validate, persist and apply a partial change.

        Returns a result rather than raising: an exception crossing the bridge
        reaches JavaScript as an opaque rejection, which cannot be shown next
        to the field that caused it.

        `persisted` is false when the change took effect but could not be
        written — the page says so instead of pretending it will survive a
        restart.
        """
        try:
            updated = self.settings.merge(patch)
        except SettingsError as exc:
            return {"ok": False, "field": exc.field, "error": str(exc)}
        except Exception:
            log.exception("could not read the settings patch")
            return {"ok": False, "field": "", "error": "could not read those settings"}

        self.settings = updated
        persisted = self.store.save(updated)
        if self._on_settings_changed is not None:
            try:
                self._on_settings_changed(updated)
            except Exception:
                log.exception("could not apply settings to the running app")
        return {"ok": True, "persisted": persisted, "settings": updated.to_json()}

    def copy(self, clip_id: str) -> bool:
        entry = self.history.get(clip_id)
        if entry is None or not entry.text:
            return False
        try:
            self.clipboard.copy(entry.text)
            return True
        except Exception:
            log.exception("could not copy to the clipboard")
            return False

    def delete(self, clip_id: str) -> bool:
        return self.history.delete(clip_id)

    def clear(self) -> int:
        return self.history.clear()

    # -- shaping -----------------------------------------------------------

    @staticmethod
    def _row(entry: Interaction) -> dict:
        """Raw-ish fields; the frontend does the formatting."""
        started = entry.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        return {
            "id": entry.id,
            "startedAt": started.isoformat(),
            "duration": round(entry.duration, 2),
            "text": entry.text,
            "error": entry.error,
            "ok": entry.ok,
            "cost": entry.cost,
            "model": entry.model,
            "latency": entry.latency,
        }
