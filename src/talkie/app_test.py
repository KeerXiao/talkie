"""The push-to-talk state machine, with the audio, network and paste layers faked."""

import threading
import time
from dataclasses import replace

import pytest

from talkie.app import Talkie
from talkie.audio import Clip
from talkie.client import ServerError, Transcript
from talkie.config import Config
from talkie.history import History


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


class FakePlayer:
    def __init__(self):
        self.cues = []

    def start(self):
        self.cues.append("start")

    def stop(self):
        self.cues.append("stop")

    def error(self):
        self.cues.append("error")


@pytest.fixture
def sound():
    return FakePlayer()


def build(recorder=None, client=None, paster=None, sound=None):
    config = Config(api_key="sk-test")
    return Talkie(
        config,
        recorder=recorder or FakeRecorder(),
        client=client or FakeClient(),
        paster=paster or FakePaster(),
        sound=sound or FakePlayer(),
    )


def dictate(app, settle=0.3):
    """Drive one full press/release cycle through the real chord listener."""
    from pynput import keyboard as kb

    app.listener.handle_press(kb.Key.ctrl_l)
    app.listener.handle_press(kb.KeyCode.from_char("\x11"))
    app.listener.handle_release(kb.KeyCode.from_char("\x11"))
    app.listener.handle_release(kb.Key.ctrl_l)
    time.sleep(settle)


def test_dictation_records_transcribes_and_pastes(sound):
    recorder, paster = FakeRecorder(), FakePaster()
    app = build(recorder=recorder, paster=paster, sound=sound)

    dictate(app)

    assert recorder.events == ["start", "stop"]
    assert paster.pasted == ["hi"]
    assert sound.cues == ["start", "stop"]
    assert (app._recording, app._busy) == (False, False)


def test_short_tap_makes_no_request(sound):
    recorder = FakeRecorder(duration=0.12)
    client = FakeClient()
    app = build(recorder=recorder, client=client, sound=sound)

    dictate(app)

    assert recorder.events == ["start", "stop"]
    assert client.calls == 0
    assert app._busy is False
    # the cues still fire: they report the key, not the outcome
    assert sound.cues == ["start", "stop"]


def test_hotkey_is_ignored_while_a_transcription_is_in_flight(sound):
    block = threading.Event()
    recorder = FakeRecorder()
    app = build(recorder=recorder, client=FakeClient(block=block), sound=sound)

    dictate(app, settle=0.1)
    assert app._busy is True

    dictate(app, settle=0.1)  # second attempt while busy
    assert recorder.events.count("start") == 1
    assert sound.cues.count("start") == 1  # no cue for an ignored press

    block.set()
    time.sleep(0.3)
    assert app._busy is False


def test_failed_transcription_beeps_and_pastes_nothing(sound):
    paster = FakePaster()
    app = build(client=FakeClient(error=ServerError("upstream down", 503)),
                paster=paster, sound=sound)

    dictate(app)

    assert paster.pasted == []
    assert sound.cues == ["start", "stop", "error"]
    assert (app._recording, app._busy) == (False, False)


def test_empty_transcript_beeps_rather_than_pasting_nothing(sound):
    paster = FakePaster()
    app = build(client=FakeClient(text=""), paster=paster, sound=sound)

    dictate(app)

    assert paster.pasted == []
    assert sound.cues[-1] == "error"


def test_unexpected_worker_failure_is_contained(sound):
    paster = FakePaster()
    app = build(client=FakeClient(error=RuntimeError("boom")), paster=paster,
                sound=sound)

    dictate(app)

    assert paster.pasted == []
    assert sound.cues[-1] == "error"
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


def test_start_cue_fires_before_the_mic_opens(sound):
    """Cue first, so the microphone records as little of it as possible."""
    order = []

    class OrderedRecorder(FakeRecorder):
        def start(self):
            order.append("mic")
            super().start()

    class OrderedPlayer(FakePlayer):
        def start(self):
            order.append("cue")
            super().start()

    app = build(recorder=OrderedRecorder(), sound=OrderedPlayer())
    dictate(app)
    assert order[:2] == ["cue", "mic"]


def test_stop_cue_fires_after_the_mic_closes(sound):
    order = []

    class OrderedRecorder(FakeRecorder):
        def stop(self):
            order.append("mic")
            return super().stop()

    class OrderedPlayer(FakePlayer):
        def stop(self):
            order.append("cue")
            super().stop()

    app = build(recorder=OrderedRecorder(), sound=OrderedPlayer())
    dictate(app)
    assert order == ["mic", "cue"]


# -- history and UI observers (M2) ----------------------------------------


def build_observed(tmp_path, client=None, keep=50):
    """A Talkie wired to real history plus recording observers."""
    from talkie.history import History

    states, records = [], []
    app = Talkie(
        Config(api_key="sk-test"),
        recorder=FakeRecorder(),
        client=client or FakeClient(),
        paster=FakePaster(),
        sound=FakePlayer(),
        history=History(root=tmp_path / "h", keep=keep),
        on_state=states.append,
        on_record=records.append,
    )
    return app, states, records


def test_a_successful_dictation_is_written_to_history(tmp_path):
    app, _, records = build_observed(tmp_path)
    dictate(app)

    entries = app.history.list()
    assert len(entries) == 1
    assert entries[0].text == "hi"
    assert entries[0].ok
    assert app.history.audio(entries[0].id) == b"RIFF"
    assert [e.text for e in records] == ["hi"]


def test_a_failed_dictation_is_written_too(tmp_path):
    app, _, _ = build_observed(tmp_path, client=FakeClient(error=ServerError("boom")))
    dictate(app)

    entries = app.history.list()
    assert len(entries) == 1
    assert not entries[0].ok
    assert "boom" in entries[0].error
    # Replayable, which is the whole reason failures are stored.
    assert app.history.audio(entries[0].id) == b"RIFF"


def test_state_transitions_drive_the_icon(tmp_path):
    app, states, _ = build_observed(tmp_path)
    dictate(app)
    assert states == ["recording", "transcribing", "idle"]


def test_a_failure_ends_in_the_error_state(tmp_path):
    app, states, _ = build_observed(tmp_path, client=FakeClient(error=ServerError("x")))
    dictate(app)
    assert states[-1] == "error"


def test_a_discarded_tap_returns_to_idle_without_history(tmp_path):
    app, states, _ = build_observed(tmp_path)
    app.recorder.duration = 0.1  # under min_seconds
    dictate(app)

    assert states == ["recording", "idle"]
    assert app.history.list() == []


def test_an_exploding_observer_never_breaks_dictation(tmp_path):
    from talkie.history import History

    def boom(_):
        raise RuntimeError("bad UI")

    paster = FakePaster()
    app = Talkie(
        Config(api_key="sk-test"),
        recorder=FakeRecorder(),
        client=FakeClient(),
        paster=paster,
        sound=FakePlayer(),
        history=History(root=tmp_path / "h"),
        on_state=boom,
        on_record=boom,
    )
    dictate(app)
    assert paster.pasted == ["hi"]  # the transcript still landed


def test_history_is_optional(tmp_path):
    """The terminal build passes no history and must behave exactly as before."""
    app = build()
    assert app.history is None
    dictate(app)
    assert app.paster.pasted == ["hi"]


# -- applying settings without a restart -----------------------------------


def test_apply_rebuilds_the_client_for_the_new_model():
    """A client is bound to one model, so a change means a new one (DESIGN 2)."""
    built = []

    def factory(config):
        built.append((config.model, config.language))
        return FakeClient()

    app = Talkie(
        Config(api_key="sk-test", model="first", language="en"),
        recorder=FakeRecorder(),
        paster=FakePaster(),
        sound=FakePlayer(),
        client_factory=factory,
    )
    assert built == [("first", "en")]

    app.apply(replace(app.config, model="second", language=None))

    assert built == [("first", "en"), ("second", None)]
    assert app.config.model == "second"


def test_apply_retunes_the_cues_and_the_history_cap(tmp_path):
    history = History(root=tmp_path, keep=50)
    app = Talkie(
        Config(api_key="sk-test"),
        recorder=FakeRecorder(),
        client=FakeClient(),
        paster=FakePaster(),
        sound=FakePlayer(),
        history=history,
    )

    app.apply(replace(app.config, sound_volume=0.0, history_keep=5))

    assert app.sound.volume == 0.0
    assert history.keep == 5


def test_lowering_retention_prunes_immediately(tmp_path):
    history = History(root=tmp_path, keep=50)
    for _ in range(6):
        history.record(Clip(b"RIFF", 1.0), transcript=Transcript("x", "m", 0.1, {}))
    app = Talkie(
        Config(api_key="sk-test"),
        recorder=FakeRecorder(),
        client=FakeClient(),
        paster=FakePaster(),
        sound=FakePlayer(),
        history=history,
    )

    app.apply(replace(app.config, history_keep=2))

    assert len(history.list()) == 2


def test_the_next_dictation_uses_the_new_client(sound):
    """The point of applying live: no restart between changing it and using it."""
    first, second = FakeClient(text="old"), FakeClient(text="new")
    clients = iter([first, second])
    paster = FakePaster()
    app = Talkie(
        Config(api_key="sk-test"),
        recorder=FakeRecorder(),
        paster=paster,
        sound=sound,
        client_factory=lambda _: next(clients),
    )

    dictate(app)
    app.apply(replace(app.config, language="zh"))
    dictate(app)

    assert paster.pasted == ["old", "new"]
