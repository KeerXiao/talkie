"""Backend clients. `talkie.client.base` is the seam; the rest are impls."""

from talkie.client.base import (
    KeyInfo,
    StreamingClient,
    StreamingSession,
    Transcript,
    TranscriptionClient,
)
from talkie.client.errors import (
    AuthError,
    ClientError,
    InsufficientCreditsError,
    NetworkError,
    RateLimitError,
    ResponseError,
    ServerError,
)
from talkie.client.openai import OpenAIClient, OpenAIStreamingClient
from talkie.client.openrouter import BASE_URL, OpenRouterClient

__all__ = [
    "AuthError",
    "BASE_URL",
    "ClientError",
    "InsufficientCreditsError",
    "KeyInfo",
    "NetworkError",
    "OpenAIClient",
    "OpenAIStreamingClient",
    "OpenRouterClient",
    "RateLimitError",
    "ResponseError",
    "ServerError",
    "StreamingClient",
    "StreamingSession",
    "Transcript",
    "TranscriptionClient",
]
