"""The OpenRouter client: request shape, error mapping and retries."""

import base64

import pytest
import requests

from talkie.audio import Clip
from talkie.client import (
    AuthError,
    ClientError,
    InsufficientCreditsError,
    NetworkError,
    OpenRouterClient,
    RateLimitError,
    ResponseError,
    ServerError,
    Transcript,
    TranscriptionClient,
)

CLIP = Clip(b"RIFF-fake-wav", 1.0)


class FakeResponse:
    def __init__(self, status_code=200, body=None, text="", reason=""):
        self.status_code = status_code
        self._body = body
        self.text = text
        self.reason = reason

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


class FakeSession:
    """Replays a queue of responses/exceptions and records every request."""

    def __init__(self, *results):
        self.results = list(results)
        self.requests = []

    def request(self, method, url, headers=None, json=None, timeout=None):
        self.requests.append(
            {"method": method, "url": url, "headers": headers, "body": json,
             "timeout": timeout}
        )
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(result, Exception):
            raise result
        return result


def ok(text="hello", usage=None):
    return FakeResponse(body={"text": text, "usage": usage or {}})


def build(*results, **kwargs):
    kwargs.setdefault("backoff", 0)  # no real sleeping in tests
    return OpenRouterClient(
        api_key="sk-test",
        model="microsoft/mai-transcribe-2",
        session=FakeSession(*results),
        **kwargs,
    )


# -- contract ----------------------------------------------------------------

def test_satisfies_the_client_protocol():
    assert isinstance(build(ok()), TranscriptionClient)


# -- request shape -----------------------------------------------------------

def test_request_shape():
    client = build(ok())
    client.transcribe(CLIP)

    sent = client._session.requests[0]
    assert sent["method"] == "POST"
    assert sent["url"] == "https://openrouter.ai/api/v1/audio/transcriptions"
    assert sent["headers"]["Authorization"] == "Bearer sk-test"
    assert sent["headers"]["Content-Type"] == "application/json"
    assert sent["timeout"] == 30.0
    assert sent["body"]["model"] == "microsoft/mai-transcribe-2"
    assert sent["body"]["input_audio"]["format"] == "wav"
    assert sent["body"]["language"] == "en"


def test_audio_is_raw_base64_not_a_data_uri():
    client = build(ok())
    client.transcribe(CLIP)

    data = client._session.requests[0]["body"]["input_audio"]["data"]
    assert not data.startswith("data:")
    assert base64.b64decode(data) == CLIP.wav


def test_language_is_omitted_when_auto_detecting():
    client = build(ok(), language=None)
    client.transcribe(CLIP)
    assert "language" not in client._session.requests[0]["body"]


def test_base_url_is_configurable_and_normalised():
    client = build(ok(), base_url="http://localhost:8080/v1/")
    client.transcribe(CLIP)
    assert client._session.requests[0]["url"] == (
        "http://localhost:8080/v1/audio/transcriptions"
    )


# -- response handling -------------------------------------------------------

def test_transcript_is_stripped_and_carries_model_and_usage():
    client = build(ok(text="  hello world \n", usage={"cost": 0.0005, "seconds": 4}))
    transcript = client.transcribe(CLIP)

    assert transcript.text == "hello world"
    assert transcript.model == "microsoft/mai-transcribe-2"
    assert transcript.cost == 0.0005
    assert transcript.latency >= 0
    assert transcript


def test_empty_transcript_is_falsy_and_has_no_cost():
    transcript = build(ok(text="   ")).transcribe(CLIP)
    assert transcript.text == ""
    assert not transcript
    assert transcript.cost is None


def test_non_numeric_cost_reads_as_none():
    assert Transcript("hi", "m", 0.1, {"cost": "free"}).cost is None


def test_key_info_unwraps_the_data_envelope():
    client = build(FakeResponse(body={"data": {"label": "laptop", "usage": 1.5}}))
    assert client.key_info() == {"label": "laptop", "usage": 1.5}
    assert client._session.requests[0]["method"] == "GET"
    assert client._session.requests[0]["url"].endswith("/key")


# -- error mapping -----------------------------------------------------------

@pytest.mark.parametrize(
    "status, expected",
    [
        (401, AuthError),
        (403, AuthError),
        (402, InsufficientCreditsError),
        (429, RateLimitError),
        (500, ServerError),
        (503, ServerError),
        (418, ClientError),
    ],
)
def test_status_codes_map_to_types(status, expected):
    client = build(
        FakeResponse(status_code=status, body={"error": {"message": "nope"}}),
        max_retries=0,
    )
    with pytest.raises(expected) as exc:
        client.transcribe(CLIP)
    assert exc.value.status == status
    assert "nope" in str(exc.value)


def test_openrouter_error_message_is_unwrapped():
    client = build(
        FakeResponse(status_code=402, body={"error": {"message": "Insufficient credits"}}),
        max_retries=0,
    )
    with pytest.raises(InsufficientCreditsError, match="402.*Insufficient credits"):
        client.transcribe(CLIP)


def test_non_json_error_falls_back_to_the_raw_body():
    client = build(FakeResponse(status_code=502, text="<html>bad gateway</html>"),
                   max_retries=0)
    with pytest.raises(ServerError, match="bad gateway"):
        client.transcribe(CLIP)


def test_network_failure_becomes_a_network_error():
    client = build(requests.ConnectionError("no route to host"), max_retries=0)
    with pytest.raises(NetworkError, match="no route to host"):
        client.transcribe(CLIP)


def test_malformed_success_body_becomes_a_response_error():
    client = build(FakeResponse(body=None, text="<html>502</html>"))
    with pytest.raises(ResponseError, match="malformed"):
        client.transcribe(CLIP)


def test_non_dict_success_body_becomes_a_response_error():
    client = build(FakeResponse(body=["unexpected"]))
    with pytest.raises(ResponseError, match="unexpected"):
        client.transcribe(CLIP)


def test_every_failure_is_a_client_error():
    """app.py catches ClientError; nothing may escape that net."""
    for error in (NetworkError, AuthError, InsufficientCreditsError,
                  RateLimitError, ServerError, ResponseError):
        assert issubclass(error, ClientError)


# -- retries -----------------------------------------------------------------

def test_retries_a_server_error_then_succeeds():
    client = build(FakeResponse(status_code=503, text="upstream down"), ok(text="hi"))
    assert client.transcribe(CLIP).text == "hi"
    assert len(client._session.requests) == 2


def test_retries_are_bounded():
    client = build(FakeResponse(status_code=503, text="down"), max_retries=2)
    with pytest.raises(ServerError):
        client.transcribe(CLIP)
    assert len(client._session.requests) == 3  # the original plus two retries


def test_auth_failure_is_not_retried():
    """A bad key will still be bad in 500ms; fail fast instead."""
    client = build(FakeResponse(status_code=401, text="bad key"))
    with pytest.raises(AuthError):
        client.transcribe(CLIP)
    assert len(client._session.requests) == 1


def test_rate_limits_and_network_blips_are_retryable():
    assert RateLimitError("x").retryable
    assert NetworkError("x").retryable
    assert ServerError("x").retryable
    assert not AuthError("x").retryable
    assert not InsufficientCreditsError("x").retryable
    assert not ResponseError("x").retryable


def test_check_names_the_key_and_what_is_left():
    client = build(FakeResponse(body={"data": {"label": "laptop", "usage": 1.5, "limit": 10.0}}))
    info = client.check()
    assert info.label == "laptop"
    assert "8.50" in info.detail


def test_check_calls_an_unlimited_key_unlimited():
    client = build(FakeResponse(body={"data": {"label": "laptop", "usage": 1.5}}))
    assert "unlimited" in client.check().detail
