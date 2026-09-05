"""The window's lifecycle rules, with pywebview's Window faked."""

from pathlib import Path

import pytest

from talkie.ui.window import MISSING_UI, HistoryWindow


class FakeEvents:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, fn):
        self.handlers.append(fn)
        return self

    def __isub__(self, fn):
        self.handlers.remove(fn)
        return self


class FakeWindow:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.events = type("E", (), {"closing": FakeEvents()})()
        self.calls = []
        self.js = []
        self.explode = False

    def show(self):
        if self.explode:
            raise RuntimeError("no window")
        self.calls.append("show")

    def hide(self):
        self.calls.append("hide")

    def destroy(self):
        self.calls.append("destroy")

    def evaluate_js(self, script):
        if self.explode:
            raise RuntimeError("not ready")
        self.js.append(script)


@pytest.fixture
def window(monkeypatch, tmp_path):
    made = {}

    def create_window(**kwargs):
        made["w"] = FakeWindow(**kwargs)
        return made["w"]

    monkeypatch.setattr("talkie.ui.window.webview.create_window", create_window)
    index = tmp_path / "index.html"
    index.write_text("<h1>ui</h1>")
    w = HistoryWindow(api=object(), index=index)
    w.create()
    return w


def test_the_window_is_visible_at_launch(window):
    """talkie is a regular app: it opens with a window, like any other."""
    assert window.window.kwargs["hidden"] is False


def test_it_can_still_be_created_hidden(monkeypatch, tmp_path):
    made = {}
    monkeypatch.setattr(
        "talkie.ui.window.webview.create_window",
        lambda **kw: made.setdefault("w", FakeWindow(**kw)),
    )
    index = tmp_path / "index.html"
    index.write_text("<h1>ui</h1>")
    HistoryWindow(api=object(), index=index).create(hidden=True)
    assert made["w"].kwargs["hidden"] is True


def test_closing_hides_instead_of_destroying(window):
    """Destroying the last window ends webview.start() and kills the menu bar."""
    assert window._on_closing() is False  # veto
    assert window.window.calls == ["hide"]


def test_show_reopens_it(window):
    window.show()
    assert window.window.calls == ["show"]


def test_refresh_notifies_the_page(window):
    window.refresh()
    assert "onHistoryChanged" in window.window.js[0]


def test_refresh_is_silent_before_the_page_is_ready(window):
    window.window.explode = True
    window.refresh()  # must not raise into the worker thread


def test_show_survives_a_dead_window(window):
    window.window.explode = True
    window.show()


def test_destroy_removes_the_close_veto_first(window):
    """Without this the destroy would be vetoed and the app could never quit."""
    assert window._on_closing in window.window.events.closing.handlers
    window.destroy()
    assert window._on_closing not in window.window.events.closing.handlers
    assert "destroy" in window.window.calls


def test_an_unbuilt_frontend_explains_itself(monkeypatch, tmp_path):
    made = {}
    monkeypatch.setattr(
        "talkie.ui.window.webview.create_window",
        lambda **kw: made.setdefault("w", FakeWindow(**kw)),
    )
    w = HistoryWindow(api=object(), index=tmp_path / "does-not-exist.html")
    w.create()
    assert w.window.kwargs.get("html") == MISSING_UI
    assert "make web" in MISSING_UI  # the target that builds it, not the one that runs it


def test_methods_are_safe_before_create():
    w = HistoryWindow(api=object(), index=Path("/nope"))
    w.show(); w.refresh(); w.destroy()  # all no-ops, none may raise
