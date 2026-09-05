"""Clipboard borrow-and-restore, and the ordering that makes ⌘V land."""

import pytest
from pynput import keyboard as kb

from talkie.paste import Paster


class FakeController:
    """Records keystrokes in the order they were synthesised."""

    def __init__(self):
        self.events = []

    def pressed(self, key):
        controller = self

        class Held:
            def __enter__(self):
                controller.events.append(f"down:{key}")

            def __exit__(self, *exc):
                controller.events.append(f"up:{key}")

        return Held()

    def press(self, key):
        self.events.append(f"press:{key}")

    def release(self, key):
        self.events.append(f"release:{key}")


@pytest.fixture(autouse=True)
def trusted(monkeypatch):
    """Accessibility is granted unless a test says otherwise."""
    monkeypatch.setattr("talkie.paste.accessibility_trusted", lambda: True)


@pytest.fixture
def clipboard(monkeypatch):
    """A fake pyperclip that logs every read and write."""

    state = {"value": "previous contents", "log": [], "readable": True}

    def paste():
        if not state["readable"]:
            raise RuntimeError("clipboard holds an image")
        state["log"].append(("read", state["value"]))
        return state["value"]

    def copy(text):
        state["value"] = text
        state["log"].append(("write", text))

    monkeypatch.setattr("talkie.paste.pyperclip.paste", paste)
    monkeypatch.setattr("talkie.paste.pyperclip.copy", copy)
    return state


def build(clipboard):
    controller = FakeController()
    return Paster(settle=0, controller=controller), controller


def test_pastes_the_text_with_cmd_v(clipboard):
    paster, controller = build(clipboard)
    paster.paste("hello world")
    assert controller.events == [
        f"down:{kb.Key.cmd}",
        "press:v",
        "release:v",
        f"up:{kb.Key.cmd}",
    ]


def test_previous_clipboard_is_restored(clipboard):
    paster, _ = build(clipboard)
    paster.paste("hello world")
    assert clipboard["value"] == "previous contents"
    assert clipboard["log"] == [
        ("read", "previous contents"),
        ("write", "hello world"),
        ("write", "previous contents"),
    ]


def test_an_unreadable_clipboard_is_not_restored(clipboard):
    """A clipboard holding an image reads as an error; don't clobber it."""
    clipboard["readable"] = False
    paster, _ = build(clipboard)
    paster.paste("hello world")
    assert clipboard["log"] == [("write", "hello world")]


def test_before_hook_runs_after_the_copy_but_prior_to_cmd_v(clipboard):
    """The chord must be released before ⌘V, or Ctrl modifies the shortcut."""
    order = []
    paster, controller = build(clipboard)

    def before():
        order.append(("before", clipboard["value"], list(controller.events)))

    paster.paste("hello world", before=before)

    (_, clipboard_at_call, keys_at_call), = order
    assert clipboard_at_call == "hello world"  # text already staged
    assert keys_at_call == []  # nothing typed yet


def test_before_is_optional(clipboard):
    paster, controller = build(clipboard)
    paster.paste("hello world", before=None)
    assert "press:v" in controller.events


def test_without_accessibility_the_transcript_is_left_on_the_clipboard(
    clipboard, monkeypatch, caplog
):
    """⌘V would no-op, so restoring the clipboard would destroy the transcript."""
    monkeypatch.setattr("talkie.paste.accessibility_trusted", lambda: False)
    paster, controller = build(clipboard)

    with caplog.at_level("ERROR"):
        paster.paste("hello world")

    assert clipboard["value"] == "hello world"  # still there to paste by hand
    assert clipboard["log"] == [("write", "hello world")]  # no restore
    assert controller.events == []  # no pointless keystrokes
    assert "Accessibility" in caplog.text
