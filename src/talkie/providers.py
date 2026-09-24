"""Which clouds talkie can talk to, what their models can do, and what they cost.

One table, because four places need the same answers and must never disagree:

  - `config.py` — which key to read, and whether the chosen mode is possible
  - `cli.py` and `app.py` — which client to build, and how to drive it
  - the settings page — what to offer, and what to grey out
  - the history window — what a clip cost, when the provider does not say

Capability belongs to the *model*, not the provider. OpenRouter's
`/audio/transcriptions` is batch-only whatever model you name; OpenAI ships
both kinds in one catalogue. So `modes` is a per-model tuple, and a model that
lists only one mode cannot be run in the other — `gpt-live-transcribe` is not
served by the file endpoint at all.

An unlisted model id is assumed batch-only. Both fields are free text on
purpose (the catalogues move faster than this file), and guessing "yes, it
streams" would fail at the worst possible moment: mid-dictation, mic open.

Prices are per minute of audio, taken from the provider's model pages. They are
here only for backends that bill by duration *and* report the duration back —
then the number in the history window is arithmetic, not an estimate of usage.
A model priced per token carries None, and its clips show no cost rather than a
figure nobody can check.
"""

from __future__ import annotations

from dataclasses import dataclass

OPENROUTER = "openrouter"
OPENAI = "openai"

# The two ways a model can be driven. The user picks one; the model has to
# offer it. They are never combined for a single clip.
STREAM = "stream"
BATCH = "batch"
MODES = (STREAM, BATCH)

MODE_LABELS = {
    STREAM: "Streaming",
    BATCH: "One-shot",
}


@dataclass(frozen=True)
class Model:
    """One transcription model: how it can be driven, and what a minute costs."""

    id: str
    label: str
    modes: tuple[str, ...] = (BATCH,)
    price_per_minute: float | None = None
    note: str = ""

    def supports(self, mode: str) -> bool:
        return mode in self.modes

    @property
    def default_mode(self) -> str:
        """Streaming when it is on offer — it is why you would pick this model."""
        return STREAM if STREAM in self.modes else BATCH

    def price(self, seconds: float) -> float | None:
        """USD for `seconds` of audio, or None when this model is not priced here."""
        if self.price_per_minute is None:
            return None
        return round(max(0.0, seconds) / 60.0 * self.price_per_minute, 6)

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "modes": list(self.modes),
            "defaultMode": self.default_mode,
            "pricePerMinute": self.price_per_minute,
            "note": self.note,
        }


@dataclass(frozen=True)
class Provider:
    """One cloud: where its key comes from, and what it can be asked for."""

    id: str
    label: str
    env_var: str
    default_model: str
    models: tuple[Model, ...]
    note: str = ""

    @property
    def streams(self) -> bool:
        return any(m.supports(STREAM) for m in self.models)

    def model(self, model_id: str) -> Model | None:
        """The catalogue entry, or None for a model id typed by hand."""
        for model in self.models:
            if model.id == model_id:
                return model
        return None

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "envVar": self.env_var,
            "defaultModel": self.default_model,
            "models": [m.to_json() for m in self.models],
            "streams": self.streams,
            "note": self.note,
        }


PROVIDERS: dict[str, Provider] = {
    OPENROUTER: Provider(
        id=OPENROUTER,
        label="OpenRouter",
        env_var="OPENROUTER_API_KEY",
        default_model="microsoft/mai-transcribe-2",
        models=(
            Model("microsoft/mai-transcribe-2", "MAI Transcribe 2"),
            Model("openai/whisper-large-v3-turbo", "Whisper large v3 turbo"),
        ),
        note="One key reaches every speech model, and every request is priced in "
        "the response. The endpoint is batch-only — nothing here streams.",
    ),
    OPENAI: Provider(
        id=OPENAI,
        label="OpenAI",
        env_var="OPENAI_API_KEY",
        default_model="gpt-live-transcribe",
        models=(
            # Realtime-session models: the only two that emit text while you
            # are still speaking, and the only two the file endpoint refuses.
            Model(
                "gpt-live-transcribe",
                "Live Transcribe",
                modes=(STREAM,),
                price_per_minute=0.017,
                note="Built for live streams. Partials arrive as you speak.",
            ),
            Model(
                "gpt-realtime-whisper",
                "Realtime Whisper",
                modes=(STREAM,),
                price_per_minute=0.017,
                note="Whisper over a realtime socket, with an accuracy/latency knob.",
            ),
            # File models. gpt-transcribe and the 4o pair also run inside a
            # realtime session, but there they transcribe only once the turn is
            # committed — which is what one-shot already does, over a socket.
            # Offering that as "streaming" would promise partials that never come.
            Model("gpt-transcribe", "GPT Transcribe", price_per_minute=0.0045),
            Model("gpt-4o-transcribe", "GPT-4o Transcribe"),
            Model("gpt-4o-mini-transcribe", "GPT-4o mini Transcribe"),
            Model("whisper-1", "Whisper v1", price_per_minute=0.006),
        ),
        note="The only provider here that streams. Billed per minute of audio, so "
        "live models cost roughly ten times a one-shot request.",
    ),
}

DEFAULT_PROVIDER = OPENROUTER
DEFAULT_MODE = BATCH


def get(provider_id: str) -> Provider:
    """The provider, falling back to the default for an unknown id.

    Never raises: this is called with values from settings.json and the
    environment, and an unrecognised one must not stop the app from starting.
    """
    return PROVIDERS.get(provider_id, PROVIDERS[DEFAULT_PROVIDER])


def known(provider_id: str) -> bool:
    return provider_id in PROVIDERS


def model(provider_id: str, model_id: str) -> Model | None:
    return get(provider_id).model(model_id)


def modes_for(provider_id: str, model_id: str) -> tuple[str, ...]:
    """Which modes this pair can be run in. Unlisted models are batch-only."""
    found = model(provider_id, model_id)
    return found.modes if found else (BATCH,)


def supports(provider_id: str, model_id: str, mode: str) -> bool:
    return mode in modes_for(provider_id, model_id)


def resolve_mode(provider_id: str, model_id: str, mode: str) -> str:
    """The mode that will actually run, given what the model offers.

    A model supporting exactly one mode wins over the stored preference: it is
    the user's more recent, more specific choice, and the alternative — refusing
    to dictate because settings.json remembers `stream` from a different model —
    helps nobody. The settings page shows what was resolved.
    """
    available = modes_for(provider_id, model_id)
    if mode in available:
        return mode
    return available[0]


def default_model(provider_id: str) -> str:
    return get(provider_id).default_model


def price(provider_id: str, model_id: str, seconds: float) -> float | None:
    """USD for `seconds` of audio, or None when the model is not priced here."""
    found = model(provider_id, model_id)
    return found.price(seconds) if found else None


def to_json() -> list[dict]:
    """The whole catalogue, for the settings page to render."""
    return [p.to_json() for p in PROVIDERS.values()]
