"""talkie — push-to-talk dictation through OpenRouter."""

from talkie.audio import Clip, Recorder
from talkie.client import ClientError, OpenRouterClient, Transcript, TranscriptionClient
from talkie.config import Config, ConfigError
from talkie.hotkey import Chord, ChordListener, parse_hotkey

__all__ = [
    "Chord",
    "ChordListener",
    "ClientError",
    "Clip",
    "Config",
    "ConfigError",
    "OpenRouterClient",
    "Recorder",
    "Transcript",
    "TranscriptionClient",
    "parse_hotkey",
]
__version__ = "0.1.0"
