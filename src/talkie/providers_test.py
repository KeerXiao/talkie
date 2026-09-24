import pytest

from talkie import providers
from talkie.providers import BATCH, OPENAI, OPENROUTER, STREAM


def test_every_provider_lists_its_own_default_model():
    for provider in providers.PROVIDERS.values():
        assert provider.model(provider.default_model) is not None


def test_every_model_offers_at_least_one_mode():
    for provider in providers.PROVIDERS.values():
        for model in provider.models:
            assert model.modes
            assert set(model.modes) <= set(providers.MODES)


def test_openrouter_streams_nothing():
    assert not providers.get(OPENROUTER).streams
    for model in providers.get(OPENROUTER).models:
        assert model.modes == (BATCH,)


def test_openai_is_the_streaming_provider():
    assert providers.get(OPENAI).streams
    assert providers.supports(OPENAI, "gpt-live-transcribe", STREAM)


def test_live_models_are_not_offered_as_batch():
    """They are not served by the file endpoint, so offering it would 404."""
    assert not providers.supports(OPENAI, "gpt-live-transcribe", BATCH)
    assert not providers.supports(OPENAI, "gpt-realtime-whisper", BATCH)


def test_an_unknown_model_is_assumed_batch_only():
    assert providers.modes_for(OPENAI, "gpt-9-whatever") == (BATCH,)
    assert not providers.supports(OPENROUTER, "some/new-model", STREAM)


def test_an_unknown_provider_falls_back_rather_than_raising():
    assert providers.get("azure").id == providers.DEFAULT_PROVIDER
    assert not providers.known("azure")


def test_resolve_mode_keeps_a_supported_preference():
    assert providers.resolve_mode(OPENAI, "gpt-live-transcribe", STREAM) == STREAM
    assert providers.resolve_mode(OPENROUTER, "microsoft/mai-transcribe-2", BATCH) == BATCH


def test_resolve_mode_overrides_an_impossible_one():
    """A one-mode model wins over whatever settings.json remembers."""
    assert providers.resolve_mode(OPENAI, "gpt-live-transcribe", BATCH) == STREAM
    assert providers.resolve_mode(OPENROUTER, "microsoft/mai-transcribe-2", STREAM) == BATCH


def test_default_mode_prefers_streaming_when_offered():
    assert providers.get(OPENAI).model("gpt-live-transcribe").default_mode == STREAM
    assert providers.get(OPENAI).model("whisper-1").default_mode == BATCH


@pytest.mark.parametrize(
    "model_id,seconds,expected",
    [
        ("gpt-live-transcribe", 60, 0.017),
        ("gpt-live-transcribe", 30, 0.0085),
        ("gpt-transcribe", 60, 0.0045),
        ("whisper-1", 120, 0.012),
        ("gpt-live-transcribe", 0, 0.0),
    ],
)
def test_priced_models_are_arithmetic(model_id, seconds, expected):
    assert providers.price(OPENAI, model_id, seconds) == pytest.approx(expected)


def test_token_priced_and_unknown_models_report_no_cost():
    """Better a blank than a number nobody can check."""
    assert providers.price(OPENAI, "gpt-4o-transcribe", 60) is None
    assert providers.price(OPENAI, "gpt-9-whatever", 60) is None
    # OpenRouter prices every response itself, so nothing is hardcoded here.
    assert providers.price(OPENROUTER, "microsoft/mai-transcribe-2", 60) is None


def test_json_round_trips_what_the_settings_page_needs():
    catalogue = providers.to_json()
    assert [p["id"] for p in catalogue] == list(providers.PROVIDERS)
    openai = next(p for p in catalogue if p["id"] == OPENAI)
    assert openai["envVar"] == "OPENAI_API_KEY"
    assert openai["streams"] is True
    live = next(m for m in openai["models"] if m["id"] == "gpt-live-transcribe")
    assert live["modes"] == [STREAM]
    assert live["pricePerMinute"] == 0.017
