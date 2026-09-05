import io
import wave

import numpy as np

from talkie.audio import Clip, Recorder


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
