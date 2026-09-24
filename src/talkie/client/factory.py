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
from talkie.client.base import StreamingClient, TranscriptionClient
from talkie.client.openai import OpenAIClient, OpenAIStreamingClient
from talkie.client.openrouter import OpenRouterClient
from talkie.config import Config, ConfigError


def _key(config: Config) -> str:
    """The credential, or a message naming the variable that is missing.

    Reachable from the settings page, which offers every provider whether or
    not its key was exported — so choosing one says what to set instead of
    failing at the next dictation with a 401 (SPEC §6.9 #8).
    """
    if not config.api_key:
        provider = providers.get(config.provider)
        raise ConfigError(f"{provider.label} needs {provider.env_var} — it is not set")
    return config.api_key


def for_config(config: Config) -> TranscriptionClient:
    """A one-shot client bound to one provider, model and language.

    Never a streaming client, and never a one-shot client for a model that has
    no file endpoint: a run is one mode or the other, decided by
    `Config.running_mode`, and the two are never combined (SPEC §6.4).
    """
    if config.streaming:
        # OpenAI's default model exists only inside a realtime session, so
        # this is what an OPENAI_API_KEY-only user reaches first. Posting it
        # to the file endpoint would 400 on every clip forever.
        raise ConfigError(
            f"{config.model} only runs as a live session — "
            "ask streaming_for_config for it, or pick a one-shot model"
        )
    if config.provider == providers.OPENROUTER:
        return OpenRouterClient(
            api_key=_key(config),
            model=config.model,
            language=config.language,
            timeout=config.request_timeout,
        )
    if config.provider == providers.OPENAI:
        return OpenAIClient(
            api_key=_key(config),
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


def streaming_for_config(config: Config) -> StreamingClient:
    """A streaming client for a config whose model actually streams.

    Asking for one when the running mode is one-shot is a programming error,
    not a user error: `Config.running_mode` has already had the final word on
    which of the two a clip gets, and there is no fallback between them.
    """
    if not config.streaming:
        raise ConfigError(
            f"{config.model} runs one-shot, not streaming — "
            "nothing should be asking for a live session"
        )
    if config.provider == providers.OPENAI:
        return OpenAIStreamingClient(
            api_key=_key(config),
            model=config.model,
            language=config.language,
            sample_rate=config.sample_rate,
            final_timeout=config.request_timeout,
        )
    provider = providers.get(config.provider)
    raise ConfigError(f"{provider.label} has no streaming endpoint")
