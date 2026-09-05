"""The push-to-talk state machine, with the audio, network and paste layers faked."""

import threading
import time

import pytest

from talkie.app import Talkie
from talkie.audio import Clip
from talkie.client import ServerError, Transcript
from talkie.config import Config


class FakeRecorder:
    def __init__(self, duration=1.0):
        self.duration = duration
        self.active = False
        self.events = []

    def start(self):
        self.active = True
        self.events.append("start")

    def stop(self):
        self.active = False
        self.events.append("stop")
        return Clip(b"RIFF", self.duration)


class FakeClient:
    def __init__(self, text="hi", error=None, block=None):
        self.text = text
        self.error = error
        self.block = block
        self.calls = 0

    def transcribe(self, clip):
        self.calls += 1
        if self.block is not None:
            self.block.wait(2)
        if self.error is not None:
            raise self.error
        return Transcript(text=self.text, model="fake", latency=0.1,
                          usage={"cost": 0.0005})


class FakePaster:
    def __init__(self):
        self.pasted = []

    def paste(self, text, before=None):
        if before is not None:
            before()
        self.pasted.append(text)


@pytest.fixture
def beeps(monkeypatch):
    recorded = []
    monkeypatch.setattr("talkie.app.beep", lambda: recorded.append("beep"))
    return recorded


def build(recorder=None, client=None, paster=None):
    config = Config(api_key="sk-test")
    app = Talkie(
        config,
        recorder=recorder or FakeRecorder(),
        client=client or FakeClient(),
        paster=paster or FakePaster(),
    )
    return app


def dictate(app, settle=0.3):
    """Drive one full press/release cycle through the real chord listener."""
    from pynput import keyboard as kb

    app.listener.handle_press(kb.Key.ctrl_l)
    app.listener.handle_press(kb.KeyCode.from_char("\x11"))
    app.listener.handle_release(kb.KeyCode.from_char("\x11"))
    app.listener.handle_release(kb.Key.ctrl_l)
    time.sleep(settle)


def test_dictation_records_transcribes_and_pastes(beeps):
    recorder, paster = FakeRecorder(), FakePaster()
    app = build(recorder=recorder, paster=paster)

    dictate(app)

    assert recorder.events == ["start", "stop"]
    assert paster.pasted == ["hi"]
    assert beeps == []
    assert (app._recording, app._busy) == (False, False)


def test_short_tap_makes_no_request(beeps):
    recorder = FakeRecorder(duration=0.12)
    client = FakeClient()
    app = build(recorder=recorder, client=client)

    dictate(app)

    assert recorder.events == ["start", "stop"]
    assert client.calls == 0
    assert app._busy is False


def test_hotkey_is_ignored_while_a_transcription_is_in_flight(beeps):
    block = threading.Event()
    recorder = FakeRecorder()
    app = build(recorder=recorder, client=FakeClient(block=block))

    dictate(app, settle=0.1)
    assert app._busy is True

    dictate(app, settle=0.1)  # second attempt while busy
    assert recorder.events.count("start") == 1

    block.set()
    time.sleep(0.3)
    assert app._busy is False


def test_failed_transcription_beeps_and_pastes_nothing(beeps):
    paster = FakePaster()
    app = build(client=FakeClient(error=ServerError("upstream down", 503)),
                paster=paster)

    dictate(app)

    assert paster.pasted == []
    assert beeps == ["beep"]
    assert (app._recording, app._busy) == (False, False)


def test_empty_transcript_beeps_rather_than_pasting_nothing(beeps):
    paster = FakePaster()
    app = build(client=FakeClient(text=""), paster=paster)

    dictate(app)

    assert paster.pasted == []
    assert beeps == ["beep"]


def test_unexpected_worker_failure_is_contained(beeps):
    paster = FakePaster()
    app = build(client=FakeClient(error=RuntimeError("boom")), paster=paster)

    dictate(app)

    assert paster.pasted == []
    assert beeps == ["beep"]
    assert app._busy is False  # the app stays usable


def test_paste_waits_for_the_chord_to_be_released():
    """⌘V must not fire while Ctrl is still physically down."""
    from pynput import keyboard as kb

    seen = []

    class CheckingPaster(FakePaster):
        def paste(self, text, before=None):
            before()
            seen.append("released")
            super().paste(text, before=None)

    app = build(paster=CheckingPaster())
    app.listener.handle_press(kb.Key.ctrl_l)
    app.listener.handle_press(kb.KeyCode.from_char("\x11"))
    app.listener.handle_release(kb.KeyCode.from_char("\x11"))  # ctrl still held
    time.sleep(0.3)
    assert seen == []  # blocked in wait_until_released

    app.listener.handle_release(kb.Key.ctrl_l)
    time.sleep(0.3)
    assert seen == ["released"]


def test_close_stops_an_active_recording():
    recorder = FakeRecorder()
    app = build(recorder=recorder)
    recorder.start()
    app.close()
    assert recorder.active is False
