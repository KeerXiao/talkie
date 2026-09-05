"""Typed failures from the transcription backend.

Callers care about two things: what to tell the user, and whether retrying is
worth it. Everything below answers both.
"""

from __future__ import annotations


class ClientError(Exception):
    """A request to the backend did not produce a usable transcript."""

    retryable = False

    def __init__(self, message: str, status: int | None = None) -> None:
        self.status = status
        self.message = message
        super().__init__(f"HTTP {status}: {message}" if status else message)


class NetworkError(ClientError):
    """The request never reached the backend (DNS, timeout, no route)."""

    retryable = True


class AuthError(ClientError):
    """The API key is missing, malformed or revoked."""


class InsufficientCreditsError(ClientError):
    """The account is out of credit."""


class RateLimitError(ClientError):
    """Too many requests; backing off may help."""

    retryable = True


class ServerError(ClientError):
    """The backend or the upstream provider failed."""

    retryable = True


class ResponseError(ClientError):
    """A 200 whose body was not the JSON we expect."""
