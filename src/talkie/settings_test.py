"""Everything here arrives from JavaScript or a hand-edited file."""

from __future__ import annotations

import json

import pytest

from talkie import providers
from talkie.config import Config
from talkie.settings import (
    AUTO,
    Settings,
    SettingsError,
    SettingsStore,
    models_for,
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


# -- provider and mode (M3) ------------------------------------------------


def test_switching_provider_repoints_the_key_without_a_restart():
    """The keys are already resolved from the environment; the swap picks the
    right one rather than writing anything to disk."""
    base = Config.from_env({"OPENROUTER_API_KEY": "sk-or", "OPENAI_API_KEY": "sk-oai"})
    applied = Settings.from_config(base).merge({"provider": "openai"}).apply_to(base)
    assert applied.provider == "openai"
    assert applied.api_key == "sk-oai"


def test_choosing_a_provider_with_no_key_leaves_it_empty_for_the_factory():
    """Empty rather than wrong: the factory names the variable to export
    (SPEC §6.9 #8), instead of a 401 at the next dictation."""
    base = Config.from_env({"OPENROUTER_API_KEY": "sk-or"})
    applied = Settings.from_config(base).merge({"provider": "openai"}).apply_to(base)
    assert applied.provider == "openai"
    assert applied.api_key == ""


def test_staying_on_one_provider_never_touches_the_key():
    base = config()  # built by hand, so it carries no `keys` map at all
    assert Settings(model="m").apply_to(base).api_key == "sk-test"


def test_an_unknown_provider_is_rejected():
    with pytest.raises(SettingsError, match="provider"):
        Settings().merge({"provider": "azure"})


def test_an_unknown_mode_is_rejected():
    with pytest.raises(SettingsError, match="mode"):
        Settings().merge({"mode": "realtime"})


def test_the_model_has_the_last_word_on_the_mode():
    """A stored preference never makes a model do what it cannot."""
    live = Settings(provider="openai", model="gpt-live-transcribe", mode="batch")
    assert live.running_mode == "stream"
    once = Settings(provider="openai", model="whisper-1", mode="stream")
    assert once.running_mode == "batch"


def test_the_model_suggestions_come_from_the_provider_table():
    """A second hardcoded list here is the drift providers.py exists to stop."""
    assert models_for("openai") == [m.id for m in providers.get("openai").models]
    assert "gpt-live-transcribe" in models_for("openai")
    assert "gpt-live-transcribe" not in models_for("openrouter")


def test_provider_and_mode_survive_a_round_trip_through_the_file(store):
    store.save(Settings(provider="openai", model="whisper-1", mode="stream"))
    loaded = store.load(Settings())
    assert (loaded.provider, loaded.model, loaded.mode) == ("openai", "whisper-1", "stream")


def test_a_model_stranded_by_a_provider_switch_falls_back():
    """The shape a settings.json written before provider was a setting leaves
    behind: an OpenRouter id with TALKIE_PROVIDER=openai. It 404s on the first
    clip, so it is caught at load instead."""
    stranded = Settings(provider="openai", model="microsoft/mai-transcribe-2")
    assert stranded.reconciled().model == "gpt-live-transcribe"


def test_an_unlisted_model_is_kept():
    """The field is free text on purpose — both catalogues move faster than
    providers.py, and a new id must still work."""
    fresh = Settings(provider="openai", model="gpt-5-transcribe-future")
    assert fresh.reconciled().model == "gpt-5-transcribe-future"


def test_a_model_that_belongs_where_it_is_is_left_alone():
    assert Settings(provider="openai", model="whisper-1").reconciled().model == "whisper-1"


def test_reading_a_stale_file_reconciles_it(store):
    store.path.write_text(
        '{"provider": "openai", "model": "microsoft/mai-transcribe-2"}'
    )
    merged, settings = resolve(config(), store)
    assert settings.model == "gpt-live-transcribe"
    assert merged.model == "gpt-live-transcribe"


def test_field_order_in_the_file_does_not_decide_the_outcome(store):
    """Reconciling per field would judge the model against a provider about to
    change, and lose the model when the file lists it first."""
    store.path.write_text('{"model": "whisper-1", "provider": "openai"}')
    assert store.load(Settings()).model == "whisper-1"
    store.path.write_text('{"provider": "openai", "model": "whisper-1"}')
    assert store.load(Settings()).model == "whisper-1"


def test_switching_provider_alone_does_not_strand_the_model():
    """The page sends the model with the provider, but a hand-written patch
    need not."""
    switched = Settings().merge({"provider": "openai"})
    assert switched.model == "gpt-live-transcribe"
