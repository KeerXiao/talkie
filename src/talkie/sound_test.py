"""Cue playback: non-blocking, volume-controlled, and silent when disabled."""

import threading

import pytest

from talkie.sound import ERROR, START, STOP, Player


@pytest.fixture
def afplay(monkeypatch):
    """Capture afplay invocations without making a noise."""
    calls = []
    monkeypatch.setattr("talkie.sound.subprocess.run",
                        lambda cmd, **kw: calls.append(cmd))
    return calls


def play_and_wait(player, method):
    """Cues run on daemon threads; wait for them so assertions are stable."""
    before = set(threading.enumerate())
    getattr(player, method)()
    for thread in set(threading.enumerate()) - before:
        thread.join(1)


@pytest.mark.parametrize(
    "method, sound", [("start", START), ("stop", STOP), ("error", ERROR)]
)
def test_each_cue_plays_its_sound(afplay, method, sound):
    play_and_wait(Player(volume=0.3), method)
    assert afplay == [["afplay", "-v", "0.3", str(sound)]]


def test_volume_is_passed_through(afplay):
    play_and_wait(Player(volume=2.5), "start")  # above 1 amplifies
    assert afplay[0][2] == "2.5"


def test_default_volume_does_not_attenuate(afplay):
    play_and_wait(Player(), "start")
    assert afplay[0][2] == "1.0"


def test_zero_volume_disables_cues(afplay):
    player = Player(volume=0)
    assert not player.enabled
    for method in ("start", "stop", "error"):
        play_and_wait(player, method)
    assert afplay == []


def test_playback_does_not_block_the_caller(monkeypatch):
    """The chord callbacks run this; a blocking cue would delay recording."""
    released = threading.Event()
    monkeypatch.setattr("talkie.sound.subprocess.run",
                        lambda cmd, **kw: released.wait(2))

    player = Player()
    player.start()  # returns immediately even though afplay is still "running"
    released.set()


def test_a_missing_afplay_is_not_fatal(monkeypatch):
    def boom(cmd, **kw):
        raise OSError("no afplay here")

    monkeypatch.setattr("talkie.sound.subprocess.run", boom)
    play_and_wait(Player(), "start")  # must not raise
