"""The subset of `Config` a user may change from the settings page.

`Config` is resolved once from the environment and frozen (DESIGN 1). These are
the fields the UI may then override, persisted to `~/.talkie/settings.json` so
the choice survives a restart.

Precedence, lowest to highest:

    dataclass defaults  ->  environment  ->  settings.json

Provider is part of that, and it carries the key with it: switching provider
re-points `Config.api_key` from the keys already read out of the environment,
so the swap needs no restart and still writes no credential to disk.

The UI is the user's most recent explicit choice, so it wins over a shell
export; the environment still seeds a first run, before any file exists.

The API key is deliberately absent. It stays environment-only and is never
written to disk — talkie is bring-your-own-key, and a settings file that could
leak one is a liability the project does not want.

One JSON shape serves both the file and the bridge. `Interaction` reshapes to
camelCase because the frontend formats it; settings are passed straight
through, so a second naming convention would buy nothing.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path

from talkie import providers
from talkie.config import DEFAULT_HISTORY_KEEP, DEFAULT_MODEL, Config

log = logging.getLogger(__name__)

DEFAULT_PATH = Path.home() / ".talkie" / "settings.json"

# "auto" is the wire and file spelling of Config.language = None: send no
# `language` field and let the model detect it.
AUTO = "auto"

# What the page offers. Presentation only — any code matching LANGUAGE_RE is
# accepted, so an unlisted language still works from the environment or by
# hand-editing the file.
LANGUAGES: list[dict[str, str]] = [
    {"code": AUTO, "label": "Auto-detect"},
    {"code": "en", "label": "English"},
    {"code": "zh", "label": "Chinese"},
    {"code": "ja", "label": "Japanese"},
    {"code": "ko", "label": "Korean"},
    {"code": "es", "label": "Spanish"},
    {"code": "fr", "label": "French"},
    {"code": "de", "label": "German"},
    {"code": "pt", "label": "Portuguese"},
    {"code": "it", "label": "Italian"},
    {"code": "ru", "label": "Russian"},
    {"code": "hi", "label": "Hindi"},
    {"code": "ar", "label": "Arabic"},
]

# Suggestions only; the field stays free text, because both catalogues move
# faster than this project can. The list itself comes from providers.py — it is
# the one table config, the factory, this page and the history window all read,
# and a second copy here is exactly the drift it exists to prevent.
def models_for(provider_id: str) -> list[str]:
    return [model.id for model in providers.get(provider_id).models]

# A BCP-47-ish subtag: 'en', 'zh', 'zh-hans', 'pt-br'.
LANGUAGE_RE = re.compile(r"^[a-z]{2,3}(-[a-z0-9]{2,8})?$")

MAX_VOLUME = 3.0
MAX_KEEP = 500


class SettingsError(ValueError):
    """One field was rejected. `field` says which, so the page can point at it."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


@dataclass(frozen=True)
class Settings:
    """Everything the settings page may change."""

    language: str | None = "en"  # None means auto-detect
    provider: str = providers.DEFAULT_PROVIDER
    model: str = DEFAULT_MODEL
    # The preference. What actually runs is Config.running_mode, because a
    # model that offers only one way to be driven overrides this.
    mode: str = providers.DEFAULT_MODE
    sound_volume: float = 1.0
    history_keep: int = DEFAULT_HISTORY_KEEP

    # -- conversion --------------------------------------------------------

    @classmethod
    def from_config(cls, config: Config) -> Settings:
        """The environment-derived baseline, before any file is layered on."""
        return cls(
            language=config.language,
            provider=config.provider,
            model=config.model,
            mode=config.mode,
            sound_volume=config.sound_volume,
            history_keep=config.history_keep,
        )

    def apply_to(self, config: Config) -> Config:
        """A copy of `config` carrying these choices.

        The key moves only when the provider does, re-pointed from the keys
        already resolved out of the environment — which is how the swap needs
        no restart and still writes no credential to disk. Staying on the same
        provider leaves `api_key` exactly as it was, so this never has to know
        where that key came from.

        A provider whose variable was never exported gets an empty key here;
        the factory refuses it, rather than letting a blank credential reach
        the network as a 401 at the next dictation.
        """
        same_provider = self.provider == config.provider
        return replace(
            config,
            language=self.language,
            provider=self.provider,
            api_key=config.api_key if same_provider else config.for_provider(self.provider),
            model=self.model,
            mode=self.mode,
            sound_volume=self.sound_volume,
            history_keep=self.history_keep,
        )

    def to_json(self) -> dict:
        return {
            "language": self.language or AUTO,
            "provider": self.provider,
            "model": self.model,
            "mode": self.mode,
            "sound_volume": self.sound_volume,
            "history_keep": self.history_keep,
        }

    @property
    def running_mode(self) -> str:
        """What a clip would actually run as, for a page that must grey out
        the choice the model cannot honour."""
        return providers.resolve_mode(self.provider, self.model, self.mode)

    # -- validation --------------------------------------------------------

    def merge(self, patch: dict) -> Settings:
        """Apply the fields present in `patch`, validating each.

        Every value here arrives from JavaScript or a hand-edited file, so
        nothing is trusted. Absent keys keep their current value; unknown keys
        are ignored. Raises `SettingsError` on the first bad field.
        """
        if not isinstance(patch, dict):
            raise SettingsError("", "settings must be an object")
        changes = {}
        for field, coerce in COERCE.items():
            if field in patch:
                changes[field] = coerce(patch[field])
        return replace(self, **changes)

    def tolerant_merge(self, patch: dict) -> Settings:
        """Like `merge`, but a bad field is logged and skipped, not fatal.

        For reading the file: one hand-edited typo must not throw away the
        other three settings, nor stop the app from starting.
        """
        merged = self
        for field, value in (patch or {}).items():
            if field not in COERCE:
                continue
            try:
                merged = merged.merge({field: value})
            except SettingsError as exc:
                log.warning("ignoring %s in settings: %s", field, exc)
        return merged


def _language(value) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SettingsError("language", "language must be text")
    code = value.strip().lower()
    if code in ("", AUTO):
        return None
    if not LANGUAGE_RE.match(code):
        raise SettingsError("language", f"{value!r} is not a language code")
    return code


def _model(value) -> str:
    if not isinstance(value, str):
        raise SettingsError("model", "model must be text")
    model = value.strip()
    if not model:
        raise SettingsError("model", "model cannot be empty")
    if len(model) > 200 or any(c.isspace() for c in model):
        raise SettingsError("model", f"{value!r} is not a model id")
    return model


def _provider(value) -> str:
    if not isinstance(value, str):
        raise SettingsError("provider", "provider must be text")
    provider_id = value.strip().lower()
    if not providers.known(provider_id):
        names = ", ".join(sorted(providers.PROVIDERS))
        raise SettingsError("provider", f"provider must be one of: {names}")
    return provider_id


def _mode(value) -> str:
    if not isinstance(value, str):
        raise SettingsError("mode", "mode must be text")
    mode = value.strip().lower()
    if mode not in providers.MODES:
        names = ", ".join(providers.MODES)
        raise SettingsError("mode", f"mode must be one of: {names}")
    return mode


def _sound_volume(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise SettingsError("sound_volume", "volume must be a number")
    try:
        volume = float(value)
    except ValueError:
        raise SettingsError("sound_volume", f"{value!r} is not a number") from None
    if not 0.0 <= volume <= MAX_VOLUME:
        raise SettingsError("sound_volume", f"volume must be between 0 and {MAX_VOLUME:g}")
    return round(volume, 2)


def _history_keep(value) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise SettingsError("history_keep", "retention must be a whole number")
    try:
        keep = int(value)
    except ValueError:
        raise SettingsError("history_keep", f"{value!r} is not a whole number") from None
    if not 1 <= keep <= MAX_KEEP:
        raise SettingsError("history_keep", f"keep between 1 and {MAX_KEEP} clips")
    return keep


COERCE = {
    "language": _language,
    "provider": _provider,
    "model": _model,
    "mode": _mode,
    "sound_volume": _sound_volume,
    "history_keep": _history_keep,
}


class SettingsStore:
    """Reads and writes `settings.json`. Neither direction may raise.

    A settings file is a convenience layered on a working environment: if it
    cannot be read the app starts on the environment's values, and if it cannot
    be written the change still applies to the running process — it just will
    not survive a restart, which `save` reports rather than hides.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else DEFAULT_PATH

    def load(self, defaults: Settings) -> Settings:
        """`defaults` layered with whatever the file overrides."""
        try:
            raw = json.loads(self.path.read_text())
        except FileNotFoundError:
            return defaults
        except Exception:
            log.warning("could not read %s — using the environment", self.path)
            return defaults
        if not isinstance(raw, dict):
            log.warning("%s is not a JSON object — using the environment", self.path)
            return defaults
        return defaults.tolerant_merge(raw)

    def save(self, settings: Settings) -> bool:
        """Atomic, like history writes. False if it could not be persisted."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(settings.to_json(), indent=2) + "\n")
            os.replace(tmp, self.path)
            return True
        except Exception:
            log.exception("could not write %s", self.path)
            return False


def resolve(config: Config, store: SettingsStore | None = None) -> tuple[Config, Settings]:
    """Layer the saved settings onto an environment-derived config.

    The single entry point for startup: both `cli.py` builds go through it, so
    the terminal and windowed builds always agree on what is configured.
    """
    store = store or SettingsStore()
    settings = store.load(Settings.from_config(config))
    return settings.apply_to(config), settings
