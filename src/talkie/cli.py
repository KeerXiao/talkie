"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import sys
import time

from talkie.app import Talkie
from talkie.audio import Recorder
from talkie.client import ClientError, OpenRouterClient
from talkie.config import Config, ConfigError
from talkie.permissions import ACCESSIBILITY_HINT, accessibility_trusted

log = logging.getLogger("talkie")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="talkie", description="Push-to-talk dictation through OpenRouter."
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
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return parser.parse_args(argv)


def _client(config: Config) -> OpenRouterClient:
    return OpenRouterClient(
        api_key=config.api_key,
        model=config.model,
        language=config.language,
        timeout=config.request_timeout,
    )


def check_key(config: Config) -> None:
    """Confirm the credential works before blaming the microphone."""
    info = _client(config).key_info()
    label = info.get("label") or "(unlabelled)"
    limit = info.get("limit")
    remaining = "unlimited" if limit is None else f"{limit - info.get('usage', 0):.2f}"
    log.info("key ok · %s · remaining %s", label, remaining)
    if accessibility_trusted():
        log.info("accessibility ok · pasting will work")
    else:
        log.warning("accessibility MISSING · %s", ACCESSIBILITY_HINT)


def record_once(config: Config, seconds: float) -> None:
    """Exercise the mic and the backend without needing Accessibility."""
    recorder = Recorder(config.sample_rate, config.channels)
    log.info("recording %.1fs…", seconds)
    recorder.start()
    time.sleep(seconds)
    clip = recorder.stop()
    log.info("captured %.1fs (%d bytes) → %s", clip.duration, len(clip.wav), config.model)

    transcript = _client(config).transcribe(clip)
    log.info("%.1fs · usage %s", transcript.latency, transcript.usage or "{}")
    print(transcript.text)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    # -v is for talkie's own tracing, not urllib3's connection chatter.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    try:
        config = Config.from_env()
        if args.check:
            check_key(config)
        elif args.record is not None:
            record_once(config, args.record)
        else:
            Talkie(config).run()
    except (ConfigError, ClientError) as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
