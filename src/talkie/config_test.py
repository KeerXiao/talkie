import pytest

from talkie.config import Config, ConfigError


def test_requires_an_api_key():
    with pytest.raises(ConfigError):
        Config.from_env({})


def test_defaults():
    config = Config.from_env({"OPENROUTER_API_KEY": "sk-test"})
    assert config.model == "microsoft/mai-transcribe-2"
    assert config.hotkey == "ctrl+q"
    assert config.language == "en"


def test_environment_overrides():
    config = Config.from_env(
        {
            "OPENROUTER_API_KEY": "sk-test",
            "TALKIE_MODEL": "openai/whisper-large-v3-turbo",
            "TALKIE_HOTKEY": "cmd+shift+d",
            "TALKIE_LANGUAGE": "de",
        }
    )
    assert config.model == "openai/whisper-large-v3-turbo"
    assert config.hotkey == "cmd+shift+d"
    assert config.language == "de"


def test_blank_language_means_auto_detect():
    config = Config.from_env({"OPENROUTER_API_KEY": "sk-test", "TALKIE_LANGUAGE": ""})
    assert config.language is None


def test_history_keep_defaults_and_overrides():
    assert Config.from_env({"OPENROUTER_API_KEY": "sk-test"}).history_keep == 50
    env = {"OPENROUTER_API_KEY": "sk-test", "TALKIE_HISTORY_KEEP": "10"}
    assert Config.from_env(env).history_keep == 10


def test_history_keep_must_be_a_number():
    with pytest.raises(ConfigError):
        Config.from_env({"OPENROUTER_API_KEY": "sk-test", "TALKIE_HISTORY_KEEP": "lots"})


def test_the_default_provider_is_openrouter():
    config = Config.from_env({"OPENROUTER_API_KEY": "sk-test"})
    assert config.provider == "openrouter"
    assert config.api_key == "sk-test"
    assert config.mode == "batch"


def test_a_lone_openai_key_selects_openai():
    """Exporting one key is an unambiguous choice; demanding TALKIE_PROVIDER
    as well would be pedantry."""
    config = Config.from_env({"OPENAI_API_KEY": "sk-oai"})
    assert config.provider == "openai"
    assert config.api_key == "sk-oai"
    assert config.model == "gpt-live-transcribe"


def test_openrouter_wins_when_both_keys_are_present():
    config = Config.from_env({"OPENROUTER_API_KEY": "sk-or", "OPENAI_API_KEY": "sk-oai"})
    assert config.provider == "openrouter"
    assert config.for_provider("openai") == "sk-oai"


def test_an_explicit_provider_is_honoured():
    config = Config.from_env(
        {"OPENROUTER_API_KEY": "sk-or", "OPENAI_API_KEY": "sk-oai",
         "TALKIE_PROVIDER": "openai"}
    )
    assert config.provider == "openai"
    assert config.api_key == "sk-oai"


def test_an_explicit_provider_without_its_key_names_the_variable():
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        Config.from_env({"OPENROUTER_API_KEY": "sk-or", "TALKIE_PROVIDER": "openai"})


def test_an_unknown_provider_is_rejected():
    with pytest.raises(ConfigError, match="TALKIE_PROVIDER"):
        Config.from_env({"OPENROUTER_API_KEY": "sk-or", "TALKIE_PROVIDER": "azure"})


def test_no_key_at_all_names_both_variables():
    with pytest.raises(ConfigError, match="OPENROUTER_API_KEY"):
        Config.from_env({"TALKIE_MODEL": "whatever"})


def test_mode_defaults_to_batch_and_can_be_pinned():
    env = {"OPENAI_API_KEY": "sk-oai", "TALKIE_MODEL": "whisper-1"}
    assert Config.from_env(env).mode == "batch"
    assert Config.from_env({**env, "TALKIE_MODE": "stream"}).mode == "stream"


def test_an_unknown_mode_is_rejected():
    with pytest.raises(ConfigError, match="TALKIE_MODE"):
        Config.from_env({"OPENAI_API_KEY": "sk-oai", "TALKIE_MODE": "realtime"})


def test_the_model_decides_the_mode_that_actually_runs():
    """A stored preference never makes a model do something it cannot."""
    live = Config.from_env({"OPENAI_API_KEY": "sk", "TALKIE_MODEL": "gpt-live-transcribe"})
    assert live.mode == "batch" and live.running_mode == "stream"
    assert live.streaming

    file_only = Config.from_env(
        {"OPENAI_API_KEY": "sk", "TALKIE_MODEL": "whisper-1", "TALKIE_MODE": "stream"}
    )
    assert file_only.mode == "stream" and file_only.running_mode == "batch"
    assert not file_only.streaming


def test_openrouter_never_streams_whatever_is_asked_for():
    config = Config.from_env({"OPENROUTER_API_KEY": "sk", "TALKIE_MODE": "stream"})
    assert not config.streaming
