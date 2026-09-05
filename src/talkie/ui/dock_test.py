"""The NSApplication delegate: dock reopen and Cmd+Q."""

from __future__ import annotations

import pytest

from talkie.ui import dock


@pytest.fixture
def delegate(monkeypatch):
    """dispatch() defers through the run loop; run it inline instead."""
    monkeypatch.setattr(
        "talkie.ui.dock.AppHelper.callAfter", lambda fn, *args: fn(*args)
    )
    calls = []
    d = dock._Delegate.alloc().initWithHandlers_(
        {"reopen": lambda: calls.append("reopen"), "quit": lambda: calls.append("quit")}
    )
    return d, calls


def test_clicking_the_dock_icon_reopens_a_hidden_window(delegate):
    d, calls = delegate
    assert d.applicationShouldHandleReopen_hasVisibleWindows_(None, False) is True
    assert calls == ["reopen"]


def test_the_dock_icon_does_nothing_when_a_window_is_already_up(delegate):
    d, calls = delegate
    d.applicationShouldHandleReopen_hasVisibleWindows_(None, True)
    assert calls == []


def test_cmd_q_shuts_down_through_our_own_path(delegate):
    """Terminating via AppKit would exit() without unwinding Python."""
    d, calls = delegate
    assert d.applicationShouldTerminate_(None) == dock.NS_TERMINATE_CANCEL
    assert calls == ["quit"]


def test_a_raising_handler_does_not_escape_into_the_run_loop(monkeypatch):
    monkeypatch.setattr(
        "talkie.ui.dock.AppHelper.callAfter", lambda fn, *args: fn(*args)
    )

    def boom():
        raise RuntimeError("nope")

    d = dock._Delegate.alloc().initWithHandlers_({"reopen": boom, "quit": boom})
    assert d.applicationShouldTerminate_(None) == dock.NS_TERMINATE_CANCEL
