"""TalkieApp wiring. The Cocoa parts are exercised by running it, not here."""

from __future__ import annotations

import pytest

from talkie.config import Config
from talkie.history import History
from talkie.ui.app import TalkieApp


class FakeMenuBar:
    def __init__(self):
        self.states, self.stats = [], []

    def set_state(self, state):
        self.states.append(state)

    def set_stats(self, stats):
        self.stats.append(stats)


class FakeWindow:
    def __init__(self):
        self.calls = []

    def create(self, hidden=False):
        self.calls.append(f"create(hidden={hidden})")

    def show(self):
        self.calls.append("show")

    def refresh(self):
        self.calls.append("refresh")

    def destroy(self):
        self.calls.append("destroy")


@pytest.fixture
def app(tmp_path):
    a = TalkieApp(
        Config(api_key="sk-test", sound_volume=0.0),
        history=History(root=tmp_path / "h"),
    )
    a.menubar = FakeMenuBar()
    a.window = FakeWindow()
    return a


def test_stop_is_idempotent(app):
    """Ctrl+C and the menu's Quit can both land; the second must be a no-op."""
    app.stop()
    app.stop()
    assert app.window.calls.count("destroy") == 1


def test_stop_stops_the_listener_before_destroying(app):
    app.stop()
    assert app.talkie.listener.running is False
    assert "destroy" in app.window.calls


def test_state_changes_reach_the_icon(app):
    app._on_state("recording")
    assert app.menubar.states == ["recording"]


def test_a_new_clip_refreshes_both_surfaces(app):
    app._on_record(object())
    assert app.window.calls == ["refresh"]
    assert app.menubar.stats == [app.api.stats()]


def test_observers_are_safe_before_the_menu_bar_exists(app):
    """run() wires the observers before MenuBar is constructed."""
    app.menubar = None
    app._on_state("recording")
    app._on_record(object())
    assert app.window.calls == ["refresh"]


def test_talkie_is_wired_to_the_same_history(app):
    assert app.talkie.history is app.history
    assert app.api.history is app.history


def test_becoming_regular_wires_reopen_and_quit(app, monkeypatch):
    """The dock icon reopens the window; Cmd+Q takes the same path as Ctrl+C."""
    captured = {}
    monkeypatch.setattr(
        "talkie.ui.app.dock.install",
        lambda on_reopen, on_quit: captured.update(reopen=on_reopen, quit=on_quit)
        or "delegate",
    )
    app._become_regular()

    assert app._delegate == "delegate"  # AppKit will not retain it for us
    assert captured["reopen"] == app.show_history
    assert captured["quit"] == app.stop


def test_the_pump_stops_once_stopping(app, monkeypatch):
    """Otherwise the timer would keep the run loop alive after quit."""
    scheduled = []
    monkeypatch.setattr(
        "talkie.ui.app.AppHelper.callLater",
        lambda delay, fn: scheduled.append(fn),
    )
    app._pump()
    assert len(scheduled) == 1  # rescheduled while running

    app._stopping = True
    app._pump()
    assert len(scheduled) == 1  # not rescheduled again
