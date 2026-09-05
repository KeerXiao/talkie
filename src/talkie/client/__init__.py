"""Backend clients. `talkie.client.base` is the seam; OpenRouter is the impl."""

from talkie.client.base import TranscriptionClient, Transcript
from talkie.client.errors import (
    AuthError,
    ClientError,
    InsufficientCreditsError,
    NetworkError,
    RateLimitError,
    ResponseError,
    ServerError,
)
from talkie.client.openrouter import BASE_URL, OpenRouterClient

__all__ = [
    "AuthError",
    "BASE_URL",
    "ClientError",
    "InsufficientCreditsError",
    "NetworkError",
    "OpenRouterClient",
    "RateLimitError",
    "ResponseError",
    "ServerError",
    "Transcript",
    "TranscriptionClient",
]
