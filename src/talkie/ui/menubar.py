"""The always-on surface: an NSStatusItem in the menu bar.

rumps is not used. It runs its own NSApplication loop, and pywebview needs
that loop for the history window — with rumps driving, webview.start() returns
in ~0.4s having silently done nothing. See DESIGN 4.

AppKit is not thread-safe: state changes arrive from the transcription worker,
so every mutation is bounced onto the main thread with callAfter.
"""

from __future__ import annotations

import logging

import objc
from AppKit import NSMenu, NSMenuItem, NSStatusBar, NSVariableStatusItemLength
from Foundation import NSObject
from PyObjCTools import AppHelper

from talkie import app as states

log = logging.getLogger(__name__)

ICONS = {
    states.IDLE: "🎙",
    states.RECORDING: "🔴",
    states.TRANSCRIBING: "⏳",
    states.ERROR: "⚠️",
}
LABELS = {
    states.IDLE: "Idle",
    states.RECORDING: "Recording…",
    states.TRANSCRIBING: "Transcribing…",
    states.ERROR: "Last attempt failed",
}


def icon_for(state: str) -> str:
    return ICONS.get(state, ICONS[states.IDLE])


def status_label(state: str) -> str:
    return f"Status: {LABELS.get(state, LABELS[states.IDLE])}"


def model_label(model: str, language: str | None = None) -> str:
    """'Model: microsoft/mai-transcribe-2 · en'. Language is worth a glance
    here: auto-detect and a wrong pin fail in the same silent way."""
    return f"Model: {model} · {language or 'auto'}"


def stats_label(stats: dict) -> str:
    """'Today: 14 clips · 6.2 min · $0.01', collapsing gracefully when empty."""
    clips = stats.get("clips", 0)
    if not clips:
        return "Today: nothing yet"
    minutes = stats.get("seconds", 0) / 60
    cost = stats.get("cost", 0) or 0
    parts = [
        f"{clips} clip" + ("s" if clips != 1 else ""),
        f"{minutes:.1f} min",
        f"${cost:.2f}" if cost >= 0.005 else "<$0.01",
    ]
    if stats.get("failures"):
        parts.append(f"{stats['failures']} failed")
    return "Today: " + " · ".join(parts)


class _Target(NSObject):
    """Selector target. PyObjC needs a real NSObject to receive menu actions.

    Every method on an NSObject subclass is exposed as an Objective-C selector
    unless marked otherwise, hence @objc.python_method on the helper.
    """

    def initWithHandlers_(self, handlers):
        self = objc.super(_Target, self).init()
        if self is None:
            return None
        self._handlers = handlers
        return self

    def openHistory_(self, sender):
        self.dispatch("open_history")

    def quit_(self, sender):
        self.dispatch("quit")

    @objc.python_method
    def dispatch(self, name):
        # A raising menu handler must not propagate into the Cocoa run loop.
        try:
            self._handlers[name]()
        except Exception:
            log.exception("menu action %s failed", name)


class MenuBar:
    """Owns the status item. Construct on the main thread, before the run loop."""

    def __init__(
        self,
        hotkey: str,
        model: str,
        on_open_history,
        on_quit,
        language: str | None = None,
    ) -> None:
        self._target = _Target.alloc().initWithHandlers_(
            {"open_history": on_open_history, "quit": on_quit}
        )
        self._item = NSStatusBar.systemStatusBar().statusItemWithLength_(
            NSVariableStatusItemLength
        )
        self._item.button().setTitle_(icon_for(states.IDLE))

        menu = NSMenu.alloc().init()
        self._status = self._info(menu, status_label(states.IDLE))
        self._info(menu, f"Hotkey: {hotkey}")
        # Retained: the settings page can change these while the app runs.
        self._model = self._info(menu, model_label(model, language))
        self._stats = self._info(menu, stats_label({}))
        menu.addItem_(NSMenuItem.separatorItem())
        self._action(menu, "Open History…", "openHistory:")
        menu.addItem_(NSMenuItem.separatorItem())
        self._action(menu, "Quit talkie", "quit:", key="q")
        self._item.setMenu_(menu)

    # -- menu construction -------------------------------------------------

    @staticmethod
    def _info(menu, title: str):
        item = menu.addItemWithTitle_action_keyEquivalent_(title, None, "")
        item.setEnabled_(False)
        return item

    def _action(self, menu, title: str, selector: str, key: str = ""):
        item = menu.addItemWithTitle_action_keyEquivalent_(title, selector, key)
        item.setTarget_(self._target)
        return item

    # -- updates (safe from any thread) ------------------------------------

    def set_state(self, state: str) -> None:
        AppHelper.callAfter(self._apply_state, state)

    def set_stats(self, stats: dict) -> None:
        AppHelper.callAfter(self._apply_stats, stats)

    def set_model(self, model: str, language: str | None) -> None:
        AppHelper.callAfter(self._apply_model, model, language)

    def _apply_state(self, state: str) -> None:
        self._item.button().setTitle_(icon_for(state))
        self._status.setTitle_(status_label(state))

    def _apply_stats(self, stats: dict) -> None:
        self._stats.setTitle_(stats_label(stats))

    def _apply_model(self, model: str, language: str | None) -> None:
        self._model.setTitle_(model_label(model, language))
