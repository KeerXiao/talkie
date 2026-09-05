"""The bridge the window calls. Every argument here arrives from JavaScript."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

import pytest

from talkie.audio import Clip
from talkie.client import Transcript
from talkie.history import History
from talkie.ui.api import Api


class FakeClipboard:
    def __init__(self, explode=False):
        self.value = None
        self.explode = explode

    def copy(self, text):
        if self.explode:
            raise RuntimeError("no pasteboard")
        self.value = text


@pytest.fixture
def api(tmp_path):
    return Api(History(root=tmp_path / "h"), clipboard=FakeClipboard())


def add(api, text="hello", *, seconds=1.0, wav=b"RIFFwav", error=None, when=None):
    transcript = None if error else Transcript(text, "m", 0.3, {"cost": 0.0001})
    return api.history.record(
        Clip(wav, seconds), transcript=transcript, error=error, started_at=when
    )


def test_list_history_is_newest_first_and_carries_no_audio(api):
    base = datetime(2026, 9, 4, 16, 0, tzinfo=timezone.utc)
    add(api, "older", when=base)
    add(api, "newer", when=base + timedelta(minutes=1))

    rows = api.list_history()
    assert [r["text"] for r in rows] == ["newer", "older"]
    assert all("audio" not in r and "wav" not in r for r in rows)
    assert rows[0]["ok"] is True and rows[0]["cost"] == pytest.approx(0.0001)


def test_rows_are_json_safe(api):
    import json

    add(api)
    add(api, error="rate limited")
    json.dumps(api.list_history())  # would raise on a datetime or bytes


def test_clip_audio_returns_a_playable_data_uri(api):
    entry = add(api, wav=b"RIFFaudio")
    uri = api.clip_audio(entry.id)

    assert uri.startswith("data:audio/wav;base64,")
    assert base64.b64decode(uri.split(",", 1)[1]) == b"RIFFaudio"


def test_clip_audio_is_none_when_the_audio_is_gone(api):
    entry = add(api)
    (api.history.root / f"{entry.id}.wav").unlink()
    assert api.clip_audio(entry.id) is None


def test_copy_puts_exactly_the_transcript_on_the_clipboard(api):
    entry = add(api, "let's ship the spec")
    assert api.copy(entry.id) is True
    assert api.clipboard.value == "let's ship the spec"


def test_copy_refuses_an_entry_with_no_text(api):
    entry = add(api, error="boom")
    assert api.copy(entry.id) is False
    assert api.clipboard.value is None


def test_copy_survives_a_broken_pasteboard(api):
    api.clipboard = FakeClipboard(explode=True)
    entry = add(api)
    assert api.copy(entry.id) is False  # reported, not raised into JS


def test_delete_and_clear(api):
    a, b = add(api, "one"), add(api, "two")
    assert api.delete(a.id) is True
    assert [r["text"] for r in api.list_history()] == ["two"]
    assert api.clear() == 1
    assert api.list_history() == []


def test_hostile_ids_from_javascript_are_refused(api):
    add(api)
    for hostile in ["../../etc/passwd", "", "..", "x/y"]:
        assert api.clip_audio(hostile) is None
        assert api.copy(hostile) is False
        assert api.delete(hostile) is False
    assert len(api.list_history()) == 1  # nothing was touched


def test_stats_counts_only_today(api):
    """Anchored to local midnight, not to `now` — otherwise this test fails
    whenever it runs shortly after midnight, as `now - 5 minutes` is yesterday."""
    midnight = datetime.now().astimezone().replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    add(api, seconds=2.0, when=midnight + timedelta(hours=9))
    add(api, seconds=3.0, when=midnight + timedelta(hours=10))
    add(api, seconds=9.0, when=midnight - timedelta(hours=3))  # yesterday
    add(api, seconds=4.0, when=midnight - timedelta(days=2))  # older still

    stats = api.stats()
    assert stats["clips"] == 2
    assert stats["seconds"] == pytest.approx(5.0)
    assert stats["cost"] == pytest.approx(0.0002)


def test_stats_includes_a_clip_from_one_minute_after_midnight(api):
    """The boundary itself: local midnight counts as today."""
    midnight = datetime.now().astimezone().replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    add(api, seconds=1.0, when=midnight)
    add(api, seconds=1.0, when=midnight - timedelta(seconds=1))

    assert api.stats()["clips"] == 1


def test_stats_counts_failures(api):
    add(api)
    add(api, error="boom")
    assert api.stats()["failures"] == 1


def test_stats_on_an_empty_history(api):
    assert api.stats() == {"clips": 0, "seconds": 0, "cost": 0, "failures": 0}


def test_api_does_not_import_pywebview():
    """It must stay swappable for a loopback HTTP handler (SPEC 5.4)."""
    import ast
    import inspect

    import talkie.ui.api as module

    imported = set()
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert "webview" not in imported
    assert "pywebview" not in imported
