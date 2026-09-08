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
