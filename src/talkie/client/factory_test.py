from dataclasses import replace

import pytest

from talkie import providers
from talkie.client.factory import for_config, streaming_for_config
from talkie.client.openai import OpenAIClient, OpenAIStreamingClient
from talkie.client.openrouter import OpenRouterClient
from talkie.config import Config, ConfigError


def test_openrouter_gets_an_openrouter_client():
    config = Config.from_env({"OPENROUTER_API_KEY": "sk-or"})
    client = for_config(config)
    assert isinstance(client, OpenRouterClient)
    assert client.api_key == "sk-or"
    assert client.model == config.model


def test_the_client_carries_the_configured_language_and_timeout():
    config = Config.from_env({"OPENROUTER_API_KEY": "sk-or", "TALKIE_LANGUAGE": "ja"})
    client = for_config(config)
    assert client.language == "ja"
    assert client.timeout == config.request_timeout


def test_openai_gets_an_openai_client():
    config = Config.from_env({"OPENAI_API_KEY": "sk-oai", "TALKIE_MODEL": "whisper-1"})
    assert config.provider == providers.OPENAI
    client = for_config(config)
    assert isinstance(client, OpenAIClient)
    assert client.model == "whisper-1"


def test_a_stream_only_model_is_never_handed_a_file_endpoint():
    """OpenAI's default model is stream-only, so this is the first thing an
    OPENAI_API_KEY-only user hits. Posting it would 400 on every clip."""
    config = Config.from_env({"OPENAI_API_KEY": "sk-oai"})
    assert config.model == "gpt-live-transcribe"
    with pytest.raises(ConfigError, match="live session"):
        for_config(config)


def test_an_unknown_provider_fails_here_not_downstream():
    """Otherwise a key reaches the wrong host and comes back a 401. Unreachable
    from the environment, which rejects it first; a stale settings file is not."""
    config = Config.from_env({"OPENROUTER_API_KEY": "sk-or"})
    with pytest.raises(ConfigError, match="azure"):
        for_config(replace(config, provider="azure"))


# -- streaming -----------------------------------------------------------


def test_a_streaming_model_gets_a_streaming_client():
    config = Config.from_env({"OPENAI_API_KEY": "sk-oai"})
    assert config.streaming
    client = streaming_for_config(config)
    assert isinstance(client, OpenAIStreamingClient)
    assert client.sample_rate == config.sample_rate


def test_the_two_factories_never_answer_for_the_same_config():
    """One mode per clip: whichever factory the running mode does not name
    refuses, so nothing can quietly hold both for one dictation."""
    live = Config.from_env({"OPENAI_API_KEY": "sk-oai"})
    once = Config.from_env({"OPENAI_API_KEY": "sk-oai", "TALKIE_MODEL": "whisper-1"})

    assert isinstance(streaming_for_config(live), OpenAIStreamingClient)
    with pytest.raises(ConfigError):
        for_config(live)

    assert isinstance(for_config(once), OpenAIClient)
    with pytest.raises(ConfigError):
        streaming_for_config(once)


def test_asking_a_one_shot_model_to_stream_is_refused():
    config = Config.from_env({"OPENAI_API_KEY": "sk", "TALKIE_MODEL": "whisper-1",
                              "TALKIE_MODE": "stream"})
    with pytest.raises(ConfigError, match="whisper-1"):
        streaming_for_config(config)


def test_openrouter_is_never_asked_to_stream():
    config = Config.from_env({"OPENROUTER_API_KEY": "sk-or", "TALKIE_MODE": "stream"})
    with pytest.raises(ConfigError):
        streaming_for_config(config)
