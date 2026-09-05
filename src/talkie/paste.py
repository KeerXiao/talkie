"""Deliver text to whatever app has focus, via the clipboard."""

from __future__ import annotations

import logging
import subprocess
import time
from typing import Callable

import pyperclip
from pynput import keyboard

log = logging.getLogger(__name__)

ALERT_SOUND = "/System/Library/Sounds/Basso.aiff"


def beep() -> None:
    """Audible failure signal — the user is looking at another app."""
    subprocess.run(["afplay", ALERT_SOUND], check=False)


class Paster:
    """Types text at the cursor by borrowing and restoring the clipboard."""

    def __init__(self, settle: float = 0.3, controller=None) -> None:
        self.settle = settle
        self._keyboard = controller or keyboard.Controller()

    def paste(self, text: str, before: Callable[[], None] | None = None) -> None:
        try:
            previous = pyperclip.paste()
        except Exception:  # a non-text clipboard (image, file) reads as an error
            log.debug("could not read the existing clipboard; not restoring it")
            previous = None

        pyperclip.copy(text)
        if before is not None:
            before()
        with self._keyboard.pressed(keyboard.Key.cmd):
            self._keyboard.press("v")
            self._keyboard.release("v")

        # Give the target app a moment to read the clipboard before we put the
        # old contents back.
        time.sleep(self.settle)
        if previous is not None:
            pyperclip.copy(previous)
