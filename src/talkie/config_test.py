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
