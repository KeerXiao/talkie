"""Everything here arrives from JavaScript or a hand-edited file."""

from __future__ import annotations

import json

import pytest

from talkie.config import Config
from talkie.settings import (
    AUTO,
    Settings,
    SettingsError,
    SettingsStore,
    resolve,
)


@pytest.fixture
def store(tmp_path):
    return SettingsStore(tmp_path / "settings.json")


def config(**kwargs) -> Config:
    return Config(api_key="sk-test", **kwargs)


# -- validation ------------------------------------------------------------


def test_merge_only_touches_the_fields_present():
    merged = Settings().merge({"language": "zh"})
    assert merged.language == "zh"
    assert merged.model == Settings().model
    assert merged.sound_volume == Settings().sound_volume


def test_auto_means_no_language():
    assert Settings().merge({"language": AUTO}).language is None
    assert Settings().merge({"language": ""}).language is None
    assert Settings().merge({"language": None}).language is None


def test_language_is_normalised():
    assert Settings().merge({"language": "  ZH  "}).language == "zh"
    assert Settings().merge({"language": "zh-Hans"}).language == "zh-hans"


@pytest.mark.parametrize("bad", ["english", "e", "zh_CN", "../etc", 7, ["zh"]])
def test_bad_languages_are_rejected(bad):
    with pytest.raises(SettingsError) as caught:
        Settings().merge({"language": bad})
    assert caught.value.field == "language"


@pytest.mark.parametrize("bad", ["", "   ", "two words", 3, None])
def test_bad_models_are_rejected(bad):
    with pytest.raises(SettingsError) as caught:
        Settings().merge({"model": bad})
    assert caught.value.field == "model"


def test_volume_is_bounded_and_rounded():
    assert Settings().merge({"sound_volume": 0}).sound_volume == 0.0
    assert Settings().merge({"sound_volume": "1.257"}).sound_volume == 1.26
    for bad in (-0.1, 3.1, "loud", True):
        with pytest.raises(SettingsError) as caught:
            Settings().merge({"sound_volume": bad})
        assert caught.value.field == "sound_volume"


def test_history_keep_is_bounded():
    assert Settings().merge({"history_keep": "20"}).history_keep == 20
    for bad in (0, 501, "many", False):
        with pytest.raises(SettingsError) as caught:
            Settings().merge({"history_keep": bad})
        assert caught.value.field == "history_keep"


def test_unknown_keys_are_ignored():
    """The patch comes from JavaScript; it must not be able to set api_key."""
    merged = Settings().merge({"api_key": "leaked", "sample_rate": 8000})
    assert merged == Settings()
    assert not hasattr(merged, "api_key")


def test_a_bad_patch_leaves_the_settings_alone():
    original = Settings(language="en")
    with pytest.raises(SettingsError):
        original.merge({"language": "nonsense"})
    assert original.language == "en"


# -- config round trip -----------------------------------------------------


def test_settings_never_carry_the_api_key():
    settings = Settings.from_config(config(language="zh", model="m"))
    assert "sk-test" not in json.dumps(settings.to_json())
    assert settings.language == "zh"


def test_apply_to_preserves_the_untouched_config():
    base = config(hotkey="cmd+d", request_timeout=9.0)
    applied = Settings(language="zh", model="m").apply_to(base)
    assert applied.language == "zh" and applied.model == "m"
    assert applied.api_key == "sk-test"
    assert applied.hotkey == "cmd+d" and applied.request_timeout == 9.0


def test_auto_survives_the_round_trip():
    settings = Settings.from_config(config(language=None))
    assert settings.to_json()["language"] == AUTO
    assert Settings().merge(settings.to_json()).language is None


# -- the store -------------------------------------------------------------


def test_missing_file_leaves_the_environment_in_charge(store):
    env = Settings(language="zh", model="from-env")
    assert store.load(env) == env


def test_saved_settings_beat_the_environment(store):
    store.save(Settings(language="zh", model="chosen"))
    loaded = store.load(Settings(language="en", model="from-env"))
    assert loaded.language == "zh" and loaded.model == "chosen"


def test_a_partial_file_only_overrides_what_it_names(store):
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps({"language": "ja"}))
    loaded = store.load(Settings(language="en", model="from-env", history_keep=7))
    assert loaded.language == "ja"
    assert loaded.model == "from-env" and loaded.history_keep == 7


def test_one_bad_field_does_not_discard_the_others(store):
    """A hand-edited typo must cost one setting, not the whole file."""
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps({"language": "gibberish", "history_keep": 12}))
    loaded = store.load(Settings(language="en"))
    assert loaded.language == "en"  # fell back
    assert loaded.history_keep == 12  # kept


@pytest.mark.parametrize("junk", ["{not json", "[1, 2]", '"a string"'])
def test_an_unreadable_file_is_not_fatal(store, junk):
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(junk)
    env = Settings(language="en")
    assert store.load(env) == env


def test_save_reports_failure_rather_than_raising(tmp_path):
    blocked = tmp_path / "afile"
    blocked.write_text("not a directory")
    assert SettingsStore(blocked / "settings.json").save(Settings()) is False


def test_saving_leaves_no_temp_file_behind(store):
    assert store.save(Settings()) is True
    assert [p.name for p in store.path.parent.iterdir()] == ["settings.json"]


def test_resolve_layers_the_file_over_the_environment(store):
    store.save(Settings(language="zh", history_keep=10))
    merged, settings = resolve(config(language="en", history_keep=50), store)
    assert merged.language == "zh" and merged.history_keep == 10
    assert merged.api_key == "sk-test"
    assert settings.language == "zh"
