"""OpenRouter transcription client.

Owns everything HTTP: auth, the request envelope, status-to-exception mapping
and bounded retries. Callers above this layer see clips, transcripts and
`ClientError`, never a `requests` object.
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Any

import requests

from talkie.audio import Clip
from talkie.client.base import KeyInfo, Transcript
from talkie.client.errors import (
    AuthError,
    ClientError,
    InsufficientCreditsError,
    NetworkError,
    RateLimitError,
    ResponseError,
    ServerError,
)

log = logging.getLogger(__name__)

BASE_URL = "https://openrouter.ai/api/v1"

STATUS_ERRORS: dict[int, type[ClientError]] = {
    401: AuthError,
    403: AuthError,
    402: InsufficientCreditsError,
    429: RateLimitError,
}


class OpenRouterClient:
    """Transcribes clips with one OpenRouter model.

    The model is bound at construction: a run uses one model, and swapping is a
    new client. Retries are attempted only for failures where a second try can
    plausibly succeed.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        language: str | None = "en",
        timeout: float = 30.0,
        base_url: str = BASE_URL,
        session: requests.Session | None = None,
        max_retries: int = 2,
        backoff: float = 0.5,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.language = language
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.backoff = backoff
        self._session = session or requests.Session()

    # -- endpoints ---------------------------------------------------------

    def transcribe(self, clip: Clip) -> Transcript:
        payload: dict[str, Any] = {
            "model": self.model,
            "input_audio": {
                # Raw base64 — NOT a 'data:audio/wav;base64,' URI.
                "data": base64.b64encode(clip.wav).decode(),
                "format": "wav",
            },
        }
        if self.language:
            payload["language"] = self.language

        started = time.monotonic()
        body = self._post("/audio/transcriptions", payload)
        return Transcript(
            text=(body.get("text") or "").strip(),
            model=self.model,
            latency=time.monotonic() - started,
            usage=body.get("usage") or {},
        )

    def check(self) -> KeyInfo:
        """Credential preflight: confirms the key works before recording."""
        info = self.key_info()
        limit = info.get("limit")
        remaining = "unlimited" if limit is None else f"{limit - info.get('usage', 0):.2f}"
        return KeyInfo(info.get("label") or "(unlabelled)", f"remaining {remaining}")

    def key_info(self) -> dict[str, Any]:
        """The raw /key payload. `check()` is what callers want."""
        return self._get("/key").get("data", {})

    # -- transport ---------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-OpenRouter-Title": "talkie",  # attribution on OpenRouter's app leaderboard
        }

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", path, json=payload)

    def _get(self, path: str) -> dict[str, Any]:
        return self._request("GET", path)

    def _request(self, method: str, path: str, json: Any = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        error: ClientError | None = None

        for attempt in range(self.max_retries + 1):
            try:
                response = self._session.request(
                    method, url, headers=self._headers(), json=json, timeout=self.timeout
                )
            except requests.RequestException as exc:
                error = NetworkError(f"request failed: {exc}")
            else:
                if response.status_code == 200:
                    return _decode(response)
                error = _error_for(response)

            if not error.retryable or attempt == self.max_retries:
                raise error
            delay = self.backoff * 2**attempt
            log.debug("%s (attempt %d), retrying in %.1fs", error, attempt + 1, delay)
            time.sleep(delay)

        raise error  # unreachable; the loop always returns or raises


def _decode(response: requests.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as exc:
        raise ResponseError(f"malformed response: {response.text[:300]}") from exc
    if not isinstance(body, dict):
        raise ResponseError(f"unexpected response: {response.text[:300]}")
    return body


def _error_for(response: requests.Response) -> ClientError:
    status = response.status_code
    message = _message(response)
    if status in STATUS_ERRORS:
        return STATUS_ERRORS[status](message, status)
    if status >= 500:
        return ServerError(message, status)
    return ClientError(message, status)


def _message(response: requests.Response) -> str:
    """Pull OpenRouter's {"error": {"message": ...}} out, else the raw body."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:300] or response.reason
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if isinstance(error, str):
            return error
    return response.text[:300]
