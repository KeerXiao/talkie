"""Deliver text to whatever app has focus, via the clipboard."""

from __future__ import annotations

import logging
import time
from typing import Callable

import pyperclip
from pynput import keyboard

from talkie.permissions import ACCESSIBILITY_HINT, accessibility_trusted

log = logging.getLogger(__name__)


class Paster:
    """Types text at the cursor by borrowing and restoring the clipboard."""

    def __init__(self, settle: float = 0.3, controller=None) -> None:
        self.settle = settle
        self._keyboard = controller or keyboard.Controller()

    def paste(self, text: str, before: Callable[[], None] | None = None) -> None:
        if not accessibility_trusted():
            # ⌘V would be a silent no-op, and restoring the clipboard
            # afterwards would throw the transcript away. Leave it staged so
            # the user can paste it by hand.
            pyperclip.copy(text)
            log.error("cannot paste: %s", ACCESSIBILITY_HINT)
            log.error("transcript left on the clipboard — press ⌘V to insert it")
            return

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
