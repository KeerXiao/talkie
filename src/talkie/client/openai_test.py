"""The OpenAI clients: request shape, cost arithmetic, error mapping, and the
realtime session driven against a fake socket."""

import queue
import time

import numpy as np
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
from talkie.client.openai import (
    COMMITTED,
    COMPLETED,
    DELTA,
    FAILED,
    TARGET_RATE,
    OpenAIStreamingClient,
    _error_for,
    _Resampler,
    _Session,
)

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


# -- resampling ----------------------------------------------------------


def test_the_resampler_stretches_16k_to_24k():
    """1.5 output samples per input sample, so the wire gets 50% more."""
    out = _Resampler(16000)(np.zeros(1600, dtype=np.int16))
    assert 2350 <= out.size <= 2400  # 1.5x, give or take the block seam


def test_resampling_block_by_block_matches_one_long_block():
    """The read position and the last sample carry across calls; without that
    every block boundary becomes a click, thirty times a second."""
    rng = np.random.default_rng(0)
    signal = rng.integers(-8000, 8000, 4800, dtype=np.int16)

    whole = _Resampler(16000)(signal)
    blocked = np.concatenate([_Resampler.__call__(r := _Resampler(16000), signal[:1600])]
                             + [r(signal[1600:3200]), r(signal[3200:])])

    assert abs(blocked.size - whole.size) <= 2
    shared = min(blocked.size, whole.size)
    assert np.array_equal(blocked[:shared], whole[:shared])


def test_the_resampler_survives_an_empty_or_single_sample_block():
    r = _Resampler(16000)
    assert r(np.zeros(0, dtype=np.int16)).size == 0
    assert r(np.zeros(1, dtype=np.int16)).size == 0
    assert r(np.zeros(320, dtype=np.int16)).size > 0


def test_a_matching_rate_is_passed_straight_through():
    block = np.arange(100, dtype=np.int16)
    assert np.array_equal(_Resampler(TARGET_RATE)(block), block)


def test_stereo_blocks_are_flattened_not_rejected():
    """sounddevice hands over (frames, channels); mono is still 2-D."""
    assert _Resampler(16000)(np.zeros((320, 1), dtype=np.int16)).size > 0


# -- the realtime session ------------------------------------------------


class FakeEvent:
    def __init__(self, type, **fields):
        self.type = type
        for name, value in fields.items():
            setattr(self, name, value)


class FakeConnection:
    """A realtime socket that answers a commit the way the server does."""

    def __init__(self, on_commit=None, fail_on=None):
        self.events = queue.Queue()
        self.appended = []
        self.config = None
        self.closed = False
        self._on_commit = on_commit
        self._fail_on = fail_on or ""
        outer = self

        class Session:
            def update(self, *, session):
                if outer._fail_on == "update":
                    raise RuntimeError("session.update failed")
                outer.config = session

        class Buffer:
            def append(self, *, audio):
                if outer._fail_on == "append":
                    raise RuntimeError("append failed")
                outer.appended.append(audio)

            def commit(self):
                if outer._fail_on == "commit":
                    raise RuntimeError("commit failed")
                for event in (outer._on_commit or _completed("hello there")):
                    outer.events.put(event)

        self.session = Session()
        self.input_audio_buffer = Buffer()

    def __enter__(self):
        if self._fail_on == "connect":
            raise RuntimeError("could not connect")
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False

    def __iter__(self):
        while True:
            event = self.events.get()
            if event is None:
                return
            yield event

    def close(self):
        self.closed = True
        self.events.put(None)


def _completed(text, item="item-1"):
    return [
        FakeEvent(COMMITTED, item_id=item),
        FakeEvent(DELTA, item_id=item, delta=text.split()[0]),
        FakeEvent(COMPLETED, item_id=item, transcript=text,
                  usage={"seconds": 60.0, "type": "duration"}),
    ]


def session(connection=None, model="gpt-live-transcribe", on_partial=None, **kwargs):
    connection = connection or FakeConnection()
    client = OpenAIStreamingClient(api_key="sk", model=model, client=object(), **kwargs)
    live = _Session(
        connect=lambda: connection,
        config=client.session_config(),
        resample=_Resampler(16000),
        model=model,
        sample_rate=16000,
        on_partial=on_partial,
        connect_timeout=2.0,
        final_timeout=3.0,
    )
    live.start()
    return live, connection


def speak(live, seconds=1.0, rate=16000):
    live.feed(np.zeros(int(rate * seconds), dtype=np.int16))


def test_a_session_transcribes_what_it_was_fed():
    live, connection = session()
    speak(live)
    transcript = live.finish()
    assert transcript.text == "hello there"
    assert transcript.model == "gpt-live-transcribe"
    assert connection.appended  # audio actually reached the socket


def test_partials_arrive_before_the_final_transcript():
    seen = []
    live, _ = session(on_partial=seen.append)
    speak(live)
    live.finish()
    assert seen[0] == "hello"  # the delta, before the completed event
    assert seen[-1] == "hello there"


def test_the_session_is_configured_for_push_to_talk():
    live, connection = session()
    speak(live)
    live.finish()
    audio = connection.config["audio"]["input"]
    assert connection.config["type"] == "transcription"
    # The hotkey is the turn detector; server VAD would cut a sentence at a pause.
    assert audio["turn_detection"] is None
    assert audio["format"] == {"type": "audio/pcm", "rate": 24000}
    assert audio["transcription"]["model"] == "gpt-live-transcribe"
    assert audio["transcription"]["language"] == "en"


def test_a_streamed_minute_is_priced_like_a_posted_one():
    live, _ = session(model="gpt-live-transcribe")
    speak(live)
    assert live.finish().cost == pytest.approx(0.017)  # 60s reported back


def test_pricing_falls_back_to_the_audio_actually_sent():
    """No duration in the response, so the session bills what it fed."""
    events = [FakeEvent(COMPLETED, item_id="i", transcript="hi")]
    live, _ = session(connection=FakeConnection(on_commit=events))
    speak(live, seconds=30)
    assert live.finish().cost == pytest.approx(0.0085)  # 30s at $0.017/min


def test_a_failed_transcription_event_is_a_failed_clip():
    events = [FakeEvent(FAILED, error=FakeEvent("x", message="audio too short"))]
    live, _ = session(connection=FakeConnection(on_commit=events))
    speak(live)
    with pytest.raises(ResponseError, match="audio too short"):
        live.finish()


def test_an_error_event_keeps_its_meaning():
    events = [FakeEvent("error", error=FakeEvent("x", code="insufficient_quota",
                                                 message="you're out"))]
    live, _ = session(connection=FakeConnection(on_commit=events))
    speak(live)
    with pytest.raises(InsufficientCreditsError):
        live.finish()


def test_a_socket_that_never_connects_fails_the_clip():
    live, _ = session(connection=FakeConnection(fail_on="connect"))
    speak(live)
    with pytest.raises(ClientError):
        live.finish()


def test_a_send_failure_mid_clip_fails_the_clip():
    live, _ = session(connection=FakeConnection(fail_on="append"))
    speak(live)
    with pytest.raises(ClientError):
        live.finish()


def test_a_socket_that_closes_early_is_reported_not_swallowed():
    """Otherwise a dropped connection would look like an empty transcript, and
    the user would be told they said nothing."""
    connection = FakeConnection()
    live, _ = session(connection=connection)
    speak(live)
    connection.events.put(None)  # server hangs up without transcribing
    with pytest.raises(ClientError, match="closed early"):
        live.finish()


def test_cancel_sends_no_commit_and_raises_nothing():
    live, connection = session()
    speak(live)
    live.cancel()
    assert connection.closed
    assert live.text == ""


def test_feeding_a_cancelled_session_is_harmless():
    live, _ = session()
    live.cancel()
    speak(live)  # the audio thread does not know the session is over yet


def test_an_exploding_partial_observer_never_loses_the_transcript():
    def explode(text):
        raise RuntimeError("the overlay fell over")

    live, _ = session(on_partial=explode)
    speak(live)
    assert live.finish().text == "hello there"


def test_deltas_from_several_items_are_joined_in_arrival_order():
    events = [
        FakeEvent(DELTA, item_id="a", delta="first"),
        FakeEvent(DELTA, item_id="b", delta="second"),
        FakeEvent(COMPLETED, item_id="a", transcript="first"),
    ]
    live, _ = session(connection=FakeConnection(on_commit=events))
    speak(live)
    assert live.finish().text == "first second"


# -- the streaming client ------------------------------------------------


def test_no_language_means_auto_detect():
    config = OpenAIStreamingClient("sk", "gpt-live-transcribe", language=None,
                                   client=object()).session_config()
    assert "language" not in config["audio"]["input"]["transcription"]


def test_the_delay_knob_is_only_sent_when_set():
    plain = OpenAIStreamingClient("sk", "gpt-realtime-whisper", client=object())
    tuned = OpenAIStreamingClient("sk", "gpt-realtime-whisper", delay="high",
                                  client=object())
    assert "delay" not in plain.session_config()["audio"]["input"]["transcription"]
    assert tuned.session_config()["audio"]["input"]["transcription"]["delay"] == "high"


def test_the_streaming_client_checks_the_same_credential():
    sdk = FakeSDK(FakeResult(), models=[object()])
    client = OpenAIStreamingClient("sk", "gpt-live-transcribe", client=sdk)
    assert "1 models" in client.check().detail


class FakeRealtime:
    def __init__(self):
        self.calls = []

    @property
    def realtime(self):
        outer = self

        class Realtime:
            def connect(self, **kwargs):
                outer.calls.append(kwargs)
                return FakeConnection()

        return Realtime()


def test_the_socket_is_opened_with_a_transcription_intent_and_no_model():
    """The query parameter names a realtime *session* model; a transcription
    session has none, and passing one is rejected as invalid_model mid-clip."""
    sdk = FakeRealtime()
    live = OpenAIStreamingClient("sk", "gpt-live-transcribe", client=sdk).open()
    speak(live)
    live.finish()

    assert sdk.calls == [{"extra_query": {"intent": "transcription"}}]


def test_latency_is_the_wait_after_the_key_comes_up():
    """Timing from open() would report how long the user spoke, and history
    rows would not be comparable with one-shot ones."""
    live, _ = session()
    speak(live)
    time.sleep(0.25)  # "speaking" — must not count towards latency
    assert live.finish().latency < 0.2
