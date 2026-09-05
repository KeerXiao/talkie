"""Environment-driven configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_MODEL = "microsoft/mai-transcribe-2"
DEFAULT_HOTKEY = "ctrl+q"


class ConfigError(Exception):
    """The environment is missing something talkie needs."""


@dataclass(frozen=True)
class Config:
    """Everything tunable, resolved once at startup."""

    api_key: str
    model: str = DEFAULT_MODEL
    hotkey: str = DEFAULT_HOTKEY
    language: str | None = "en"

    sample_rate: int = 16000
    channels: int = 1
    min_seconds: float = 0.3
    request_timeout: float = 30.0
    paste_settle: float = 0.3

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Config:
        env = os.environ if env is None else env
        api_key = env.get("OPENROUTER_API_KEY", "")
        if not api_key:
            raise ConfigError("OPENROUTER_API_KEY is not set")
        return cls(
            api_key=api_key,
            model=env.get("TALKIE_MODEL", DEFAULT_MODEL),
            hotkey=env.get("TALKIE_HOTKEY", DEFAULT_HOTKEY),
            # Empty means "let the model auto-detect".
            language=env.get("TALKIE_LANGUAGE", "en") or None,
        )
