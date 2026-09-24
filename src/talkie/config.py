"""Environment-driven configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from talkie import providers

DEFAULT_PROVIDER = providers.DEFAULT_PROVIDER
DEFAULT_MODEL = providers.default_model(DEFAULT_PROVIDER)
DEFAULT_MODE = providers.DEFAULT_MODE
DEFAULT_HOTKEY = "ctrl+q"
DEFAULT_HISTORY_KEEP = 50


class ConfigError(Exception):
    """The environment is missing something talkie needs."""


@dataclass(frozen=True)
class Config:
    """Everything tunable, resolved once at startup."""

    # The key for `provider`. Kept as its own field, rather than read out of
    # `keys` on demand, because every client is built from one provider's
    # credential and nothing below this layer should know there are others.
    api_key: str = ""
    provider: str = DEFAULT_PROVIDER
    model: str = DEFAULT_MODEL
    hotkey: str = DEFAULT_HOTKEY
    language: str | None = "en"

    # Every key found in the environment, by provider id. Switching provider
    # from the settings page re-points `api_key` from here, so the swap needs
    # no restart and no key on disk.
    keys: dict[str, str] = field(default_factory=dict)

    # How to drive the model: providers.STREAM or providers.BATCH. Never both
    # for one clip — the mode is chosen before the mic opens.
    mode: str = DEFAULT_MODE

    sample_rate: int = 16000
    channels: int = 1
    min_seconds: float = 0.3
    request_timeout: float = 30.0
    paste_settle: float = 0.3
    sound_volume: float = 1.0  # 0 turns the cues off; >1 amplifies
    history_keep: int = DEFAULT_HISTORY_KEEP

    @property
    def running_mode(self) -> str:
        """The mode the next clip will actually run in.

        `mode` is a preference; a model that offers only one way to be driven
        overrides it. Everything downstream asks this, never `mode`.
        """
        return providers.resolve_mode(self.provider, self.model, self.mode)

    @property
    def streaming(self) -> bool:
        return self.running_mode == providers.STREAM

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Config:
        env = os.environ if env is None else env
        keys = {p.id: env.get(p.env_var, "").strip() for p in providers.PROVIDERS.values()}
        provider = _provider(env.get("TALKIE_PROVIDER"), keys)
        return cls(
            api_key=keys[provider],
            provider=provider,
            keys=keys,
            model=env.get("TALKIE_MODEL") or providers.default_model(provider),
            hotkey=env.get("TALKIE_HOTKEY", DEFAULT_HOTKEY),
            # Empty means "let the model auto-detect".
            language=env.get("TALKIE_LANGUAGE", "en") or None,
            mode=_mode(env.get("TALKIE_MODE")),
            sound_volume=_volume(env.get("TALKIE_SOUND_VOLUME")),
            history_keep=_keep(env.get("TALKIE_HISTORY_KEEP")),
        )

    def for_provider(self, provider_id: str) -> str:
        """That provider's key, or empty if the environment never set it."""
        return self.keys.get(provider_id, "")


def _provider(raw: str | None, keys: dict[str, str]) -> str:
    """Which cloud to start on.

    An explicit TALKIE_PROVIDER is honoured even with no key, so the error
    names the variable the user meant to set. With nothing pinned, the default
    wins if it has a key and anything else that does wins if it doesn't —
    exporting only OPENAI_API_KEY is an unambiguous choice of provider, and
    demanding TALKIE_PROVIDER as well would be pedantry.
    """
    if raw:
        chosen = raw.strip().lower()
        if not providers.known(chosen):
            names = ", ".join(sorted(providers.PROVIDERS))
            raise ConfigError(f"TALKIE_PROVIDER must be one of: {names} (got {raw!r})")
        if not keys.get(chosen):
            raise ConfigError(f"{providers.get(chosen).env_var} is not set")
        return chosen

    if keys.get(DEFAULT_PROVIDER):
        return DEFAULT_PROVIDER
    for provider_id, key in keys.items():
        if key:
            return provider_id
    names = " or ".join(p.env_var for p in providers.PROVIDERS.values())
    raise ConfigError(f"no API key found — set {names}")


def _mode(raw: str | None) -> str:
    if raw is None or raw == "":
        return DEFAULT_MODE
    mode = raw.strip().lower()
    if mode not in providers.MODES:
        names = ", ".join(providers.MODES)
        raise ConfigError(f"TALKIE_MODE must be one of: {names} (got {raw!r})")
    return mode


def _volume(raw: str | None) -> float:
    if raw is None or raw == "":
        return 1.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        raise ConfigError(f"TALKIE_SOUND_VOLUME must be a number (got {raw!r})") from None


def _keep(raw: str | None) -> int:
    if raw is None or raw == "":
        return DEFAULT_HISTORY_KEEP
    try:
        return max(1, int(raw))
    except ValueError:
        raise ConfigError(
            f"TALKIE_HISTORY_KEEP must be a whole number (got {raw!r})"
        ) from None
