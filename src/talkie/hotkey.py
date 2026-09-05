"""Global push-to-talk chord detection on top of pynput."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

from pynput import keyboard

MODIFIERS = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "alt",
    "option": "alt",
    "opt": "alt",
    "cmd": "cmd",
    "command": "cmd",
    "super": "cmd",
    "shift": "shift",
}


@dataclass(frozen=True)
class Chord:
    """A set of modifiers plus exactly one ordinary key."""

    modifiers: frozenset[str]
    main: str

    @property
    def tokens(self) -> frozenset[str]:
        return self.modifiers | {self.main}

    def is_down(self, pressed: set[str]) -> bool:
        return self.tokens <= pressed

    def __str__(self) -> str:
        return "+".join([*sorted(self.modifiers), self.main])


def parse_hotkey(spec: str) -> Chord:
    """'ctrl+q' -> Chord({'ctrl'}, 'q')."""
    parts = [part.strip().lower() for part in spec.split("+") if part.strip()]
    if not parts:
        raise ValueError(f"hotkey is empty: {spec!r}")
    modifiers: set[str] = set()
    main: str | None = None
    for part in parts:
        if part in MODIFIERS:
            modifiers.add(MODIFIERS[part])
        elif main is None:
            main = part
        else:
            raise ValueError(f"hotkey needs exactly one non-modifier key: {spec!r}")
    if main is None:
        raise ValueError(f"hotkey needs a non-modifier key: {spec!r}")
    return Chord(frozenset(modifiers), main)


def normalize(key) -> str | None:
    """Map a pynput key onto a stable token: 'ctrl', 'q', 'f5'."""
    if isinstance(key, keyboard.Key):
        base = key.name.split("_")[0]  # ctrl_l -> ctrl
        return MODIFIERS.get(base, base)
    char = getattr(key, "char", None)
    if char:
        # macOS delivers ctrl+q as the control code '\x11'. Fold it back to 'q'
        # so that a press and its release produce the same token.
        if len(char) == 1 and ord(char) < 32:
            char = chr(ord(char) + 96)
        return char.lower()
    vk = getattr(key, "vk", None)
    return f"vk{vk}" if vk is not None else None


class ChordListener:
    """Watches the keyboard and reports when the chord goes down and up.

    Callbacks run on the pynput listener thread, so keep them quick.
    """

    def __init__(
        self,
        chord: Chord,
        on_engage: Callable[[], None],
        on_disengage: Callable[[], None],
    ) -> None:
        self.chord = chord
        self._on_engage = on_engage
        self._on_disengage = on_disengage
        self._pressed: set[str] = set()
        self._engaged = False
        self._lock = threading.Lock()
        self._listener: keyboard.Listener | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        self._listener = keyboard.Listener(
            on_press=self.handle_press, on_release=self.handle_release
        )
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()

    @property
    def running(self) -> bool:
        return self._listener is not None and self._listener.running

    def join(self, timeout: float | None = None) -> None:
        if self._listener is not None:
            self._listener.join(timeout)

    # -- events ------------------------------------------------------------

    def handle_press(self, key) -> None:
        token = normalize(key)
        if token is None:
            return
        with self._lock:
            self._pressed.add(token)
            if self._engaged or not self.chord.is_down(self._pressed):
                return
            self._engaged = True
        self._on_engage()

    def handle_release(self, key) -> None:
        token = normalize(key)
        if token is None:
            return
        with self._lock:
            self._pressed.discard(token)
            if not self._engaged or self.chord.is_down(self._pressed):
                return
            self._engaged = False
        self._on_disengage()

    # -- queries -----------------------------------------------------------

    def wait_until_released(self, timeout: float = 2.0) -> bool:
        """Block until no chord key is physically held. False on timeout.

        Synthesised ⌘V would otherwise pick up a still-held Ctrl.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if not (self._pressed & self.chord.tokens):
                    return True
            time.sleep(0.02)
        return False
