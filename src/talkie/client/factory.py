"""Build the right client for a config.

One place that turns "provider + model + mode" into an object, because the
terminal build, the windowed build and `--check` all need the same answer and
used to each construct an `OpenRouterClient` by hand — which stopped being
correct the moment there was a second provider.

A provider talkie does not implement yet fails here, with the variable name in
the message, rather than somewhere downstream as an authentication error
against the wrong host.
"""

from __future__ import annotations

from talkie import providers
from talkie.client.base import TranscriptionClient
from talkie.client.openrouter import OpenRouterClient
from talkie.config import Config, ConfigError


def for_config(config: Config) -> TranscriptionClient:
    """A client bound to one provider, model and language.

    Never a streaming client: a session is opened per dictation, not per app,
    so `Talkie` asks for that separately when `config.streaming` says to.
    """
    if config.provider == providers.OPENROUTER:
        return OpenRouterClient(
            api_key=config.api_key,
            model=config.model,
            language=config.language,
            timeout=config.request_timeout,
        )
    provider = providers.get(config.provider)
    raise ConfigError(
        f"talkie cannot talk to {provider.label} yet — "
        f"set {providers.get(providers.OPENROUTER).env_var} and unset TALKIE_PROVIDER"
    )
