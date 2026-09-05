"""macOS privacy permissions talkie depends on.

Recording, the global hotkey and pasting are three separate grants, and the
failure modes look nothing alike:

- Microphone       — denied loudly, the stream raises.
- Input Monitoring — denied silently, the hotkey never fires.
- Accessibility    — denied silently, synthetic ⌘V is a no-op.

The last one is the dangerous one: everything upstream works, the transcript
comes back, and nothing appears. So we check it rather than guess.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

ACCESSIBILITY_HINT = (
    "System Settings → Privacy & Security → Accessibility: enable the app you "
    "launched talkie from, then quit and reopen it"
)


def accessibility_trusted() -> bool:
    """True when this process may post synthetic keystrokes.

    Non-macOS platforms have no such gate, so they report True.
    """
    try:
        from ApplicationServices import AXIsProcessTrusted
    except ImportError:
        return True
    return bool(AXIsProcessTrusted())


def prompt_for_accessibility() -> bool:
    """Ask macOS to show its 'grant Accessibility' dialog. True if trusted."""
    try:
        from ApplicationServices import (
            AXIsProcessTrustedWithOptions,
            kAXTrustedCheckOptionPrompt,
        )
    except ImportError:
        return True
    return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True}))
