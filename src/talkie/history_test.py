from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from talkie.audio import Clip
from talkie.client import Transcript
from talkie.history import History, Interaction


@pytest.fixture
def history(tmp_path):
    return History(root=tmp_path / "history", keep=3)


def clip(seconds: float = 1.0, wav: bytes = b"RIFFfake") -> Clip:
    return Clip(wav=wav, duration=seconds)


def transcript(text: str = "hello there") -> Transcript:
    return Transcript(text=text, model="m", latency=0.4, usage={"cost": 0.00008})


def test_record_writes_a_readable_pair(history):
    entry = history.record(clip(2.0), transcript())

    assert entry is not None
    assert (history.root / f"{entry.id}.json").exists()
    assert (history.root / f"{entry.id}.wav").exists()
    assert history.audio(entry.id) == b"RIFFfake"

    stored = json.loads((history.root / f"{entry.id}.json").read_text())
    assert stored["text"] == "hello there"
    assert stored["cost"] == pytest.approx(0.00008)
    assert stored["error"] is None


def test_failures_are_recorded_with_playable_audio(history):
    entry = history.record(clip(1.5), error="rate limited")

    assert entry is not None and not entry.ok
    assert entry.error == "rate limited"
    # The whole point: a failed clip can still be replayed and diagnosed.
    assert history.audio(entry.id) == b"RIFFfake"


def test_list_is_newest_first(history):
    base = datetime(2026, 9, 4, 16, 0, 0, tzinfo=timezone.utc)
    for i in range(3):
        history.record(clip(), transcript(f"clip {i}"), started_at=base + timedelta(minutes=i))

    assert [e.text for e in history.list()] == ["clip 2", "clip 1", "clip 0"]


def test_retention_drops_the_oldest(history):
    base = datetime(2026, 9, 4, 16, 0, 0, tzinfo=timezone.utc)
    for i in range(6):
        history.record(clip(), transcript(f"clip {i}"), started_at=base + timedelta(minutes=i))

    kept = history.list()
    assert len(kept) == 3  # keep=3
    assert [e.text for e in kept] == ["clip 5", "clip 4", "clip 3"]
    # Pruning takes the audio with it, not just the metadata.
    assert list(history.root.glob("*.wav")) != []
    assert len(list(history.root.glob("*.wav"))) == 3


def test_two_clips_in_one_second_both_survive(history):
    when = datetime(2026, 9, 4, 16, 0, 0, tzinfo=timezone.utc)
    a = history.record(clip(), transcript("first"), started_at=when)
    b = history.record(clip(), transcript("second"), started_at=when)

    assert a.id != b.id
    assert {e.text for e in history.list()} == {"first", "second"}


def test_delete_removes_both_files(history):
    entry = history.record(clip(), transcript())
    assert history.delete(entry.id) is True
    assert history.list() == []
    assert history.audio(entry.id) is None
    assert history.delete(entry.id) is False


def test_clear_empties_the_directory(history):
    for _ in range(3):
        history.record(clip(), transcript())
    assert history.clear() == 3
    assert history.list() == []
    assert list(history.root.glob("*")) == []


def test_ids_from_the_ui_cannot_escape_the_directory(history):
    history.record(clip(), transcript())
    for hostile in ["../../etc/passwd", "..", "", "foo/bar", "20260904T164210Z/../x"]:
        assert history.get(hostile) is None
        assert history.audio(hostile) is None
        assert history.delete(hostile) is False


def test_a_corrupt_entry_is_skipped_not_fatal(history):
    good = history.record(clip(), transcript("survivor"))
    (history.root / "20260904T999999Z.json").write_text("{ not json")

    assert [e.text for e in history.list()] == ["survivor"]
    assert history.get(good.id).text == "survivor"


def test_a_write_failure_never_raises(history, monkeypatch):
    monkeypatch.setattr(History, "_atomic_write", staticmethod(lambda *a: 1 / 0))
    # Dictation must survive a full disk.
    assert history.record(clip(), transcript()) is None


def test_no_partial_pair_is_ever_visible(history, monkeypatch):
    """A crash between the WAV and the JSON leaves no listable entry."""
    real = History._atomic_write
    calls = []

    def fail_on_json(path, payload):
        calls.append(path.suffix)
        if path.suffix == ".json":
            raise OSError("crash")
        real(path, payload)

    monkeypatch.setattr(History, "_atomic_write", staticmethod(fail_on_json))
    assert history.record(clip(), transcript()) is None
    assert calls == [".wav", ".json"]
    assert history.list() == []  # the orphan WAV is not listable


def test_roundtrip_through_json_preserves_the_entry():
    entry = Interaction(
        id="20260904T164210Z",
        started_at=datetime(2026, 9, 4, 16, 42, 10, tzinfo=timezone.utc),
        duration=2.125,
        model="microsoft/mai-transcribe-2",
        text="let's ship the spec",
        latency=0.83,
        cost=0.000083,
    )
    assert Interaction.from_json(entry.to_json()) == entry


def test_rewrite_replaces_the_outcome_and_keeps_the_identity(tmp_path):
    """A retry is the same dictation, so it keeps its id, time and audio."""
    history = History(root=tmp_path)
    entry = history.record(Clip(b"RIFF", 2.0), error="upstream down")

    updated = history.rewrite(
        entry.id, transcript=Transcript(text="got it", model="m2", latency=0.3)
    )

    assert updated.id == entry.id
    assert updated.started_at == entry.started_at
    assert updated.duration == 2.0
    assert (updated.text, updated.model, updated.error) == ("got it", "m2", None)
    assert history.audio(entry.id) == b"RIFF"
    assert len(history.list()) == 1


def test_rewrite_can_record_a_second_failure(tmp_path):
    history = History(root=tmp_path)
    entry = history.record(Clip(b"RIFF", 1.0), error="first")
    updated = history.rewrite(entry.id, error="still down")
    assert updated.error == "still down" and not updated.ok


def test_rewriting_a_clip_that_is_gone_is_not_an_error(tmp_path):
    assert History(root=tmp_path).rewrite("nope", error="x") is None
