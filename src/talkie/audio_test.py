import io
import wave

import numpy as np

from talkie.audio import Clip, Recorder, blocks, samples


def test_empty_clip_is_falsy():
    assert not Clip(b"", 0.0)
    assert Clip(b"RIFF", 1.0)


def test_stop_without_frames_returns_an_empty_clip():
    assert Recorder().stop() == Clip(b"", 0.0)


def test_packs_frames_into_a_16k_mono_wav():
    recorder = Recorder(sample_rate=16000, channels=1)
    for _ in range(5):  # 5 x 0.1s
        recorder._on_audio(np.zeros((1600, 1), dtype=np.int16), 1600, None, None)

    clip = recorder.stop()
    assert clip.duration == 0.5

    with wave.open(io.BytesIO(clip.wav)) as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 16000
        assert wav.getnframes() == 8000


def test_frames_are_dropped_after_stop():
    recorder = Recorder()
    recorder._on_audio(np.zeros((160, 1), dtype=np.int16), 160, None, None)
    recorder.stop()
    assert recorder.stop() == Clip(b"", 0.0)


def test_the_tap_sees_every_block_and_the_clip_is_unaffected():
    recorder = Recorder()
    seen = []
    recorder._on_frame = seen.append
    for _ in range(3):
        recorder._on_audio(np.zeros((1600, 1), dtype=np.int16), 1600, None, None)

    assert len(seen) == 3
    assert recorder.stop().duration == 0.3


def test_a_broken_tap_costs_the_live_transcript_not_the_recording():
    """It runs on PortAudio's callback thread — raising there ends the stream."""
    recorder = Recorder()
    calls = []

    def explode(block):
        calls.append(block)
        raise RuntimeError("the socket went away")

    recorder._on_frame = explode
    recorder._on_audio(np.zeros((1600, 1), dtype=np.int16), 1600, None, None)
    recorder._on_audio(np.zeros((1600, 1), dtype=np.int16), 1600, None, None)

    assert len(calls) == 1  # unbound after the first failure
    assert recorder.stop().duration == 0.2  # both blocks still recorded


def test_the_tap_does_not_survive_the_recording_that_installed_it():
    recorder = Recorder()
    recorder._on_frame = lambda block: None
    recorder.stop()
    assert recorder._on_frame is None


def test_a_stored_clip_decodes_back_to_what_was_recorded():
    """History keeps the WAV so a failed clip can be sent again."""
    recorder = Recorder(sample_rate=16000)
    recorder._on_audio(np.arange(1600, dtype=np.int16).reshape(-1, 1), 1600, None, None)
    clip = recorder.stop()

    frames, rate = samples(clip.wav)
    assert rate == 16000
    assert np.array_equal(frames, np.arange(1600, dtype=np.int16))


def test_the_rate_comes_from_the_file_not_the_caller():
    """A clip recorded before the config changed still plays back correctly."""
    recorder = Recorder(sample_rate=8000)
    recorder._on_audio(np.zeros((800, 1), dtype=np.int16), 800, None, None)
    assert samples(recorder.stop().wav)[1] == 8000


def test_blocks_cover_every_sample_exactly_once():
    frames = np.arange(4100, dtype=np.int16)
    chunks = blocks(frames, 1600)
    assert [len(c) for c in chunks] == [1600, 1600, 900]
    assert np.array_equal(np.concatenate(chunks), frames)
