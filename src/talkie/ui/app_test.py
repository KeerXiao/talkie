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


# -- the live overlay (M3) -------------------------------------------------


class FakeOverlay:
    def __init__(self):
        self.calls = []

    def show(self, text="", state=""):
        self.calls.append(("show", text, state))

    def update(self, text):
        self.calls.append(("update", text))

    def finish(self, text):
        self.calls.append(("finish", text))

    def settle(self):
        self.calls.append(("settle",))

    def hide(self):
        self.calls.append(("hide",))


@pytest.fixture
def overlaid(app):
    app.overlay = FakeOverlay()
    return app


def test_the_strip_appears_when_recording_starts(overlaid):
    overlaid._on_state("recording")
    assert overlaid.overlay.calls == [("show", "", "")]


def test_partials_go_straight_to_the_strip(overlaid):
    overlaid._on_partial("the quick")
    overlaid._on_partial("the quick brown")
    assert overlaid.overlay.calls == [("update", "the quick"), ("update", "the quick brown")]


def test_a_one_shot_clip_keeps_the_strip_up_while_it_waits(overlaid):
    """The overlay is not conditional on paying for streaming (SPEC §6.4)."""
    assert not overlaid.config.streaming
    overlaid._on_state("transcribing")
    assert overlaid.overlay.calls == [("show", "", "working")]


def test_a_streamed_clip_does_not_blank_the_strip_on_release(overlaid):
    """Its last partial is still the best thing to show."""
    from dataclasses import replace

    overlaid.config = replace(
        overlaid.config, provider="openai", model="gpt-live-transcribe"
    )
    assert overlaid.config.streaming
    overlaid._on_state("transcribing")
    assert overlaid.overlay.calls == []


def test_the_finished_transcript_is_shown_then_left_to_fade(overlaid, tmp_path):
    from talkie.history import Interaction

    overlaid._on_record(Interaction(id="1", started_at=None, duration=1.0,
                                    model="m", text="all done"))
    assert ("finish", "all done") in overlaid.overlay.calls


def test_a_failure_says_what_went_wrong_rather_than_vanishing(overlaid):
    from talkie.history import Interaction

    overlaid._on_record(Interaction(id="1", started_at=None, duration=1.0,
                                    model="m", text="", error="network unreachable"))
    assert ("finish", "network unreachable") in overlaid.overlay.calls


def test_quitting_takes_the_strip_down(overlaid):
    overlaid.stop()
    assert ("hide",) in overlaid.overlay.calls


def test_every_observer_survives_having_no_overlay_yet(app):
    """The observers can fire before run() has built it."""
    app.overlay = None
    app._on_state("recording")
    app._on_partial("hello")
    app.stop()


def test_a_tap_too_short_to_transcribe_takes_the_strip_down(overlaid):
    """It writes no history row, so nothing else would ever hide it."""
    overlaid._on_state("recording")
    overlaid._on_state("idle")
    assert overlaid.overlay.calls[-1] == ("settle",)


def test_a_failed_clip_also_settles(overlaid):
    overlaid._on_state("error")
    assert overlaid.overlay.calls == [("settle",)]
