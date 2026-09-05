import pytest
from pynput import keyboard as kb

from talkie.hotkey import Chord, ChordListener, normalize, parse_hotkey


def test_parses_the_default_chord():
    assert parse_hotkey("ctrl+q") == Chord(frozenset({"ctrl"}), "q")


def test_accepts_aliases_and_case():
    assert parse_hotkey("Command+Shift+D") == Chord(frozenset({"cmd", "shift"}), "d")


def test_accepts_a_bare_key():
    assert parse_hotkey("f5") == Chord(frozenset(), "f5")


@pytest.mark.parametrize("spec", ["", "ctrl", "ctrl+q+w"])
def test_rejects_unusable_specs(spec):
    with pytest.raises(ValueError):
        parse_hotkey(spec)


def test_str_round_trips():
    assert str(parse_hotkey("ctrl+q")) == "ctrl+q"


@pytest.mark.parametrize(
    "key, token",
    [
        (kb.Key.ctrl_l, "ctrl"),
        (kb.Key.ctrl_r, "ctrl"),
        (kb.Key.cmd, "cmd"),
        (kb.Key.f5, "f5"),
        (kb.KeyCode.from_char("q"), "q"),
        (kb.KeyCode.from_char("Q"), "q"),
    ],
)
def test_normalize(key, token):
    assert normalize(key) == token


def test_control_codes_fold_back_to_the_letter():
    """macOS sends ctrl+q as '\\x11'; press and release must agree."""
    assert normalize(kb.KeyCode.from_char("\x11")) == "q"


class Spy:
    def __init__(self):
        self.events = []

    def listener(self):
        return ChordListener(
            parse_hotkey("ctrl+q"),
            on_engage=lambda: self.events.append("engage"),
            on_disengage=lambda: self.events.append("disengage"),
        )


CTRL = kb.Key.ctrl_l
CTRL_Q = kb.KeyCode.from_char("\x11")
PLAIN_Q = kb.KeyCode.from_char("q")


def test_partial_chord_does_not_engage():
    spy = Spy()
    listener = spy.listener()
    listener.handle_press(CTRL)
    assert spy.events == []


def test_full_chord_engages_once():
    spy = Spy()
    listener = spy.listener()
    listener.handle_press(CTRL)
    listener.handle_press(CTRL_Q)
    listener.handle_press(CTRL_Q)  # auto-repeat must not re-engage
    assert spy.events == ["engage"]


def test_releasing_either_key_disengages():
    spy = Spy()
    listener = spy.listener()
    listener.handle_press(CTRL)
    listener.handle_press(CTRL_Q)
    listener.handle_release(CTRL)  # modifier first
    assert spy.events == ["engage", "disengage"]


def test_release_clears_state_when_the_char_arrives_unmodified():
    spy = Spy()
    listener = spy.listener()
    listener.handle_press(CTRL)
    listener.handle_press(CTRL_Q)
    listener.handle_release(CTRL)
    listener.handle_release(PLAIN_Q)  # ctrl already up, so plain 'q'
    assert listener.wait_until_released(timeout=0.1)


def test_unrelated_keys_are_inert():
    spy = Spy()
    listener = spy.listener()
    for key in (kb.Key.shift, kb.KeyCode.from_char("a"), kb.Key.space):
        listener.handle_press(key)
        listener.handle_release(key)
    assert spy.events == []


def test_wait_until_released_times_out_while_held():
    spy = Spy()
    listener = spy.listener()
    listener.handle_press(CTRL)
    assert listener.wait_until_released(timeout=0.05) is False
