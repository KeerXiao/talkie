from dataclasses import replace

import pytest

from talkie import providers
from talkie.client.factory import for_config
from talkie.client.openai import OpenAIClient
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
    config = Config.from_env({"OPENAI_API_KEY": "sk-oai"})
    assert config.provider == providers.OPENAI
    client = for_config(config)
    assert isinstance(client, OpenAIClient)
    assert client.model == "gpt-live-transcribe"


def test_an_unknown_provider_fails_here_not_downstream():
    """Otherwise a key reaches the wrong host and comes back a 401. Unreachable
    from the environment, which rejects it first; a stale settings file is not."""
    config = Config.from_env({"OPENROUTER_API_KEY": "sk-or"})
    with pytest.raises(ConfigError, match="azure"):
        for_config(replace(config, provider="azure"))
