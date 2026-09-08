"""The bridge the window calls. Every argument here arrives from JavaScript."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

import pytest

from talkie.audio import Clip
from talkie.client import Transcript
from talkie.history import History
from talkie.settings import MAX_KEEP, Settings, SettingsStore
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
    """It must stay swappable for a loopback HTTP handler (DESIGN 5)."""
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


# -- settings --------------------------------------------------------------


@pytest.fixture
def settings_api(tmp_path):
    """Api wired to a throwaway settings file, recording what it applies."""
    applied = []
    api = Api(
        History(root=tmp_path / "h"),
        clipboard=FakeClipboard(),
        settings=Settings(language="en", model="m", sound_volume=1.0, history_keep=50),
        store=SettingsStore(tmp_path / "settings.json"),
        on_settings_changed=applied.append,
    )
    api.applied = applied
    return api


def test_get_settings_carries_the_choices_the_form_needs(settings_api):
    form = settings_api.get_settings()
    assert form["settings"]["language"] == "en"
    assert {"code": "zh", "label": "Chinese"} in form["languages"]
    assert form["languages"][0]["code"] == "auto"
    assert form["models"] and form["limits"]["maxKeep"] == MAX_KEEP


def test_get_settings_never_leaks_the_key(settings_api):
    assert "api_key" not in settings_api.get_settings()["settings"]


def test_update_persists_and_applies(settings_api):
    result = settings_api.update_settings({"language": "zh"})

    assert result == {
        "ok": True,
        "persisted": True,
        "settings": {
            "language": "zh",
            "model": "m",
            "sound_volume": 1.0,
            "history_keep": 50,
        },
    }
    assert settings_api.settings.language == "zh"
    assert [s.language for s in settings_api.applied] == ["zh"]
    # Reloading the store must see it, or the change dies with the process.
    assert settings_api.store.load(Settings()).language == "zh"


def test_a_rejected_field_changes_nothing(settings_api):
    result = settings_api.update_settings({"language": "not a code"})

    assert result["ok"] is False and result["field"] == "language"
    assert settings_api.settings.language == "en"
    assert settings_api.applied == []
    assert not settings_api.store.path.exists()


@pytest.mark.parametrize("junk", ["a string", 42, None, ["language", "zh"]])
def test_a_junk_patch_is_reported_not_raised(settings_api, junk):
    """Everything here arrives from JavaScript; nothing may escape as a throw."""
    result = settings_api.update_settings(junk)
    assert result["ok"] is False
    assert settings_api.applied == []


def test_auto_detect_round_trips(settings_api):
    assert settings_api.update_settings({"language": "auto"})["ok"] is True
    assert settings_api.settings.language is None
    assert settings_api.get_settings()["settings"]["language"] == "auto"
    assert settings_api.applied[-1].language is None


def test_a_broken_observer_does_not_lose_the_save(tmp_path):
    """The settings are already written; a failure to apply must not undo that."""

    def explode(_):
        raise RuntimeError("menu bar is gone")

    api = Api(
        History(root=tmp_path / "h"),
        clipboard=FakeClipboard(),
        store=SettingsStore(tmp_path / "settings.json"),
        on_settings_changed=explode,
    )
    assert api.update_settings({"model": "other"})["ok"] is True
    assert api.settings.model == "other"


def test_an_unwritable_store_still_applies(tmp_path):
    blocked = tmp_path / "afile"
    blocked.write_text("not a directory")
    api = Api(
        History(root=tmp_path / "h"),
        clipboard=FakeClipboard(),
        store=SettingsStore(blocked / "settings.json"),
    )
    result = api.update_settings({"model": "other"})
    assert result["ok"] is True and result["persisted"] is False
    assert api.settings.model == "other"
