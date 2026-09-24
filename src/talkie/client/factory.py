"""Build the right client for a config.

One place that turns "provider + model" into an object, because the terminal
build, the windowed build and `--check` all need the same answer and used to
each construct an `OpenRouterClient` by hand — which stopped being correct the
moment there was a second provider.

A provider talkie does not implement fails here, rather than somewhere
downstream as an authentication error against the wrong host.
"""

from __future__ import annotations

from talkie import providers
from talkie.client.base import TranscriptionClient
from talkie.client.openai import OpenAIClient
from talkie.client.openrouter import OpenRouterClient
from talkie.config import Config, ConfigError


def for_config(config: Config) -> TranscriptionClient:
    """A one-shot client bound to one provider, model and language.

    Never a streaming client: a realtime session is opened per dictation, not
    per app, so it is asked for separately when the mode calls for it.
    """
    if config.provider == providers.OPENROUTER:
        return OpenRouterClient(
            api_key=config.api_key,
            model=config.model,
            language=config.language,
            timeout=config.request_timeout,
        )
    if config.provider == providers.OPENAI:
        return OpenAIClient(
            api_key=config.api_key,
            model=config.model,
            language=config.language,
            timeout=config.request_timeout,
        )
    # Unreachable from `Config.from_env`, which rejects an unknown provider —
    # but reachable from a settings file written by a later version.
    provider = providers.get(config.provider)
    names = " or ".join(p.env_var for p in providers.PROVIDERS.values())
    raise ConfigError(
        f"talkie cannot talk to {config.provider!r} — "
        f"pick {provider.label} or another provider, and set {names}"
    )
