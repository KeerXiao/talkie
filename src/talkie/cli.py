"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import sys
import time

from talkie.app import Talkie
from talkie.audio import Recorder
from talkie.client import ClientError, StreamingClient, TranscriptionClient
from talkie.client.factory import for_config, streaming_for_config
from talkie.config import Config, ConfigError
from talkie.permissions import ACCESSIBILITY_HINT, accessibility_trusted
from talkie.settings import resolve

log = logging.getLogger("talkie")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="talkie", description="Push-to-talk dictation through OpenRouter or OpenAI."
    )
    parser.add_argument(
        "--record",
        type=float,
        metavar="SECONDS",
        help="record for SECONDS and print the transcript, without hotkeys or pasting",
    )
    parser.add_argument(
        "--check", action="store_true", help="verify the API key and exit"
    )
    parser.add_argument(
        "--ui",
        action="store_true",
        help="run as a windowed app with history, instead of headless",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return parser.parse_args(argv)


def _client(config: Config) -> TranscriptionClient | StreamingClient:
    """Whichever client the running mode calls for. Both can `check()`."""
    return streaming_for_config(config) if config.streaming else for_config(config)


def check_key(config: Config) -> None:
    """Confirm the credential works before blaming the microphone.

    What a provider will say about a key differs — OpenRouter quotes a
    balance, OpenAI only confirms it works — so this prints whatever came back
    rather than reaching for fields one backend happens to have.
    """
    log.info("key ok · %s · %s", config.provider, _client(config).check())
    if accessibility_trusted():
        log.info("accessibility ok · pasting will work")
    else:
        log.warning("accessibility MISSING · %s", ACCESSIBILITY_HINT)


def record_once(config: Config, seconds: float) -> None:
    """Exercise the mic and the backend without needing Accessibility.

    Streaming runs the real path — a session opened before the mic and fed as
    the audio arrives — because a one-shot request for a live model is exactly
    what the rest of the code refuses to make.
    """
    recorder = Recorder(config.sample_rate, config.channels)
    client = _client(config)
    session = client.open(on_partial=_show_partial) if config.streaming else None

    log.info("recording %.1fs…", seconds)
    recorder.start(on_frame=session.feed if session else None)
    time.sleep(seconds)
    clip = recorder.stop()
    log.info("captured %.1fs (%d bytes) → %s", clip.duration, len(clip.wav), config.model)

    try:
        transcript = session.finish() if session else client.transcribe(clip)
    finally:
        if session is not None:
            session.cancel()  # a no-op once finish() has returned
    log.info("%.1fs · usage %s", transcript.latency, transcript.usage or "{}")
    print(transcript.text)


def _show_partial(text: str) -> None:
    """Partials go to the log in this milestone; the overlay comes next."""
    log.info("… %s", text)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    # -v is for talkie's own tracing, not the HTTP stacks' connection chatter.
    # httpx2 is what the OpenAI SDK vendors, and it logs every request at INFO.
    # The `openai` logger itself is left alone: its only INFO line says a
    # request is being retried, which is exactly what -v is for.
    for noisy in ("urllib3", "httpx2", "websockets"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        # The settings page writes ~/.talkie/settings.json; both builds layer
        # it over the environment here, so `make run` and `make ui` never
        # disagree about which model or language is in use.
        config, settings = resolve(Config.from_env())
        if args.check:
            check_key(config)
        elif args.record is not None:
            record_once(config, args.record)
        elif args.ui:
            # Imported lazily: the terminal build must not need PyObjC or WebKit.
            from talkie.ui.app import TalkieApp

            TalkieApp(config, settings=settings).run()
        else:
            Talkie(config).run()
    except (ConfigError, ClientError) as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
