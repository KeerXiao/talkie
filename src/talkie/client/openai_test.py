"""The OpenAI one-shot client: request shape, cost arithmetic, error mapping."""

import openai
import pytest

from talkie.audio import Clip
from talkie.client import (
    AuthError,
    ClientError,
    InsufficientCreditsError,
    NetworkError,
    OpenAIClient,
    RateLimitError,
    ResponseError,
    ServerError,
    TranscriptionClient,
)
from talkie.client.openai import _error_for

CLIP = Clip(b"RIFF-fake-wav", 60.0)


class FakeResult:
    def __init__(self, text="hello", usage=None, duration=None):
        self.text = text
        if usage is not None:
            self.usage = usage
        if duration is not None:
            self.duration = duration


class FakeTranscriptions:
    """Records the call and replays one result, or raises one exception."""

    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


class FakeSDK:
    def __init__(self, result=None, error=None, models=None):
        self.audio = type("Audio", (), {"transcriptions": FakeTranscriptions(result, error)})()
        self._models = models

    @property
    def transcriptions(self):
        return self.audio.transcriptions

    @property
    def models(self):
        outer = self

        class Models:
            def list(self_inner):
                if isinstance(outer._models, Exception):
                    raise outer._models
                return type("Page", (), {"data": outer._models or []})()

        return Models()


def client(**kwargs):
    sdk = kwargs.pop("sdk", None) or FakeSDK(FakeResult())
    kwargs.setdefault("model", "whisper-1")
    return OpenAIClient(api_key="sk-oai", client=sdk, **kwargs), sdk


def test_satisfies_the_client_protocol():
    assert isinstance(client()[0], TranscriptionClient)


def test_sends_the_clip_as_a_named_in_memory_file():
    """The SDK infers the part's content type from the name, so a bare bytes
    payload would be posted as the wrong type."""
    c, sdk = client()
    c.transcribe(CLIP)
    sent = sdk.transcriptions.calls[0]
    assert sent["model"] == "whisper-1"
    assert sent["file"] == ("clip.wav", CLIP.wav, "audio/wav")
    assert sent["language"] == "en"


def test_auto_detect_sends_no_language():
    c, sdk = client(language=None)
    c.transcribe(CLIP)
    assert "language" not in sdk.transcriptions.calls[0]


def test_returns_the_stripped_transcript():
    c, _ = client(sdk=FakeSDK(FakeResult(text="  hello there \n")))
    assert c.transcribe(CLIP).text == "hello there"


def test_a_response_without_text_is_a_response_error():
    c, _ = client(sdk=FakeSDK(FakeResult(text=None)))
    with pytest.raises(ResponseError):
        c.transcribe(CLIP)


# -- cost ----------------------------------------------------------------


def test_a_minute_of_audio_is_priced_from_the_table():
    """OpenAI never quotes a price, so the figure is arithmetic. whisper-1 is
    $0.006/min, so a 60s clip costs $0.006."""
    c, _ = client(model="whisper-1")
    assert c.transcribe(CLIP).cost == pytest.approx(0.006)


def test_a_token_priced_model_shows_no_cost_rather_than_zero():
    c, _ = client(model="gpt-4o-transcribe")
    transcript = c.transcribe(CLIP)
    assert transcript.cost is None
    assert "cost" not in transcript.usage


def test_the_reported_duration_wins_over_the_recorded_one():
    """It is what OpenAI actually billed for."""
    c, _ = client(sdk=FakeSDK(FakeResult(usage={"seconds": 120})))
    assert c.transcribe(CLIP).cost == pytest.approx(0.012)


def test_the_reported_usage_is_kept_alongside_the_price():
    c, _ = client(sdk=FakeSDK(FakeResult(usage={"seconds": 30, "type": "duration"})))
    usage = c.transcribe(CLIP).usage
    assert usage["seconds"] == 30 and usage["type"] == "duration"
    assert usage["cost"] == pytest.approx(0.003)


def test_a_pydantic_usage_object_is_flattened():
    class Usage:
        def model_dump(self):
            return {"seconds": 60}

    c, _ = client(sdk=FakeSDK(FakeResult(usage=Usage())))
    assert c.transcribe(CLIP).usage == {"seconds": 60, "cost": pytest.approx(0.006)}


# -- credential check ----------------------------------------------------


def test_check_counts_the_models_it_can_see():
    c, _ = client(sdk=FakeSDK(FakeResult(), models=[object(), object()]))
    info = c.check()
    assert "2 models" in info.detail


def test_check_surfaces_a_bad_key_as_an_auth_error():
    c, _ = client(sdk=FakeSDK(FakeResult(), models=_status(openai.AuthenticationError, 401)))
    with pytest.raises(AuthError):
        c.check()


# -- error mapping -------------------------------------------------------


def _status(kind, status, body=None):
    import httpx2

    request = httpx2.Request("POST", "https://api.openai.com/v1/audio/transcriptions")
    return kind("boom", response=httpx2.Response(status, request=request), body=body)


@pytest.mark.parametrize(
    "exc, expected",
    [
        (_status(openai.AuthenticationError, 401), AuthError),
        (_status(openai.PermissionDeniedError, 403), AuthError),
        (_status(openai.RateLimitError, 429), RateLimitError),
        (_status(openai.InternalServerError, 503), ServerError),
        (_status(openai.NotFoundError, 404), ClientError),
    ],
)
def test_sdk_exceptions_become_talkie_errors(exc, expected):
    c, _ = client(sdk=FakeSDK(error=exc))
    with pytest.raises(expected) as caught:
        c.transcribe(CLIP)
    assert caught.value.status == exc.status_code


def test_a_spent_quota_is_credit_not_rate_limiting():
    """OpenAI bills an empty account as a 429; OpenRouter uses a 402. The user
    needs to be told to top up, not to slow down."""
    exc = _status(openai.RateLimitError, 429, body={"code": "insufficient_quota"})
    c, _ = client(sdk=FakeSDK(error=exc))
    with pytest.raises(InsufficientCreditsError):
        c.transcribe(CLIP)


def test_a_connection_failure_is_retryable():
    import httpx2

    exc = openai.APIConnectionError(
        message="no route", request=httpx2.Request("POST", "https://api.openai.com/")
    )
    c, _ = client(sdk=FakeSDK(error=exc))
    with pytest.raises(NetworkError) as caught:
        c.transcribe(CLIP)
    assert caught.value.retryable


def test_an_unrecognised_failure_still_arrives_as_a_client_error():
    """Nothing above this layer should have to catch a bare Exception."""
    c, _ = client(sdk=FakeSDK(error=ValueError("something odd")))
    with pytest.raises(ClientError) as caught:
        c.transcribe(CLIP)
    assert "something odd" in str(caught.value)


def test_a_talkie_error_passes_through_unwrapped():
    assert isinstance(_error_for(AuthError("nope", 401)), AuthError)
