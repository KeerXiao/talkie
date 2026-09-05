"""Audible cues.

While dictating, the user is looking at another app — there is no window to
watch — so every state change has to be audible.
"""

from __future__ import annotations

import logging
import subprocess
import threading
from pathlib import Path

log = logging.getLogger(__name__)

SOUNDS = Path("/System/Library/Sounds")

START = SOUNDS / "Tink.aiff"    # listening
STOP = SOUNDS / "Bottle.aiff"   # heard you, transcribing
ERROR = SOUNDS / "Basso.aiff"   # nothing was pasted


class Player:
    """Fire-and-forget cue playback.

    Each cue runs on a throwaway thread: `afplay` lives for the length of the
    sound, and the chord callbacks must not wait for it or recording would
    start late.

    Volume multiplies the system output level rather than replacing it, so a
    quiet Mac stays quiet. 0 disables cues; above 1 amplifies.
    """

    def __init__(self, volume: float = 1.0) -> None:
        self.volume = volume

    @property
    def enabled(self) -> bool:
        return self.volume > 0

    def start(self) -> None:
        self.play(START)

    def stop(self) -> None:
        self.play(STOP)

    def error(self) -> None:
        self.play(ERROR)

    def play(self, sound: Path) -> None:
        if not self.enabled:
            return
        threading.Thread(target=self._afplay, args=(sound,), daemon=True).start()

    def _afplay(self, sound: Path) -> None:
        try:
            subprocess.run(
                ["afplay", "-v", str(self.volume), str(sound)],
                check=False,
                capture_output=True,
            )
        except OSError as exc:  # afplay missing, or not macOS
            log.debug("could not play %s: %s", sound.name, exc)
