"""The live caption strip: a floating panel that never takes focus.

This is the window the milestone exists for — the words appear here while they
are still being spoken, instead of after they have been pasted somewhere.

**Why this is not a second WebView, and why the style mask matters.**
The transcript is about to be pasted into whatever app is frontmost, and
`paste.py` sends ⌘V to exactly that. A preview window that took key focus would
make talkie frontmost and paste the transcript into talkie. So the panel is
built from the one AppKit configuration that cannot do that — a non-activating
`NSPanel`, shown with `orderFrontRegardless()` and never `makeKeyAndOrderFront_`
— and it ignores mouse events so a click where it sits reaches the window
underneath. See DESIGN 4.1.

Partials arrive on a socket reader thread and AppKit is not thread-safe, so
every mutation is bounced onto the main thread with `callAfter`, exactly like
the menu bar.
"""

from __future__ import annotations

import logging

from AppKit import (
    NSBackingStoreBuffered,
    NSColor,
    NSFont,
    NSMakeRect,
    NSPanel,
    NSScreen,
    NSStatusWindowLevel,
    NSTextField,
    NSVisualEffectBlendingModeBehindWindow,
    NSVisualEffectStateActive,
    NSVisualEffectView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
)
from PyObjCTools import AppHelper

log = logging.getLogger(__name__)

WIDTH = 720.0
HEIGHT = 96.0
BOTTOM_MARGIN = 140.0  # clear of the Dock
CORNER_RADIUS = 16.0
FONT_SIZE = 22.0
PADDING = 18.0

# Long enough to read what was just pasted, short enough not to sit over the
# next thing the user does.
LINGER = 1.6

# What the strip reads when there is no transcript to show yet. Text always
# wins over a placeholder; these are only for the gaps.
#
# A one-shot model never sends a word before the request returns, so the wait
# has to be named rather than hinted at — a bare ellipsis looks like a stalled
# stream, which on OpenRouter is the only thing the user would ever see.
LISTENING = "Listening…"
WORKING = "Transcribing…"
NOTHING = "Nothing heard"

# The gap the strip is filling, when it is filling one.
HOLDING = "holding"    # the key is down and the mic is open
WAITING = "waiting"    # the key is up and the request is out
DONE = "done"          # the clip is finished, whatever it produced

PLACEHOLDERS = {HOLDING: LISTENING, WAITING: WORKING, DONE: NOTHING}

# A hold can run for minutes; the panel shows the end of it, which is the part
# still being spoken. Whole words only — a caption cut mid-word reads as an
# error in the transcript, which is the one thing this window exists to rule out.
MAX_CHARS = 180


def caption(text: str, state: str = HOLDING) -> str:
    """What the strip should read.

    Text wins whenever there is any. Otherwise the placeholder names the gap:
    the panel appearing blank looks like a failure, and an ellipsis that never
    grows looks like a stream that died.
    """
    text = (text or "").strip()
    if text:
        return trim(text, MAX_CHARS)
    return PLACEHOLDERS.get(state, LISTENING)


def trim(text: str, limit: int = MAX_CHARS) -> str:
    """Keep the tail — the newest words are the ones being checked."""
    if len(text) <= limit:
        return text
    tail = text[-limit:]
    spaced = tail.find(" ")
    if spaced != -1 and spaced < limit // 2:
        tail = tail[spaced + 1 :]
    return "… " + tail


def frame_for(screen, width: float = WIDTH, height: float = HEIGHT,
              margin: float = BOTTOM_MARGIN):
    """Centred horizontally, sitting above the Dock.

    Positioned against the screen's visible frame rather than its full frame,
    so the strip does not hide under the menu bar or the Dock on any display.
    """
    visible = screen.visibleFrame()
    width = min(width, visible.size.width - 40.0)
    x = visible.origin.x + (visible.size.width - width) / 2.0
    y = visible.origin.y + margin
    return NSMakeRect(x, y, width, height)


class Overlay:
    """Owns the panel. Construct on the main thread, before the run loop."""

    def __init__(self, width: float = WIDTH, height: float = HEIGHT) -> None:
        screen = NSScreen.mainScreen()
        frame = frame_for(screen, width, height) if screen else NSMakeRect(0, 0, width, height)

        self._panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            frame,
            # Borderless so there is no title bar to drag, and non-activating
            # so showing it does not make talkie the frontmost app.
            NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
            NSBackingStoreBuffered,
            False,
        )
        self._panel.setLevel_(NSStatusWindowLevel)
        self._panel.setOpaque_(False)
        self._panel.setBackgroundColor_(NSColor.clearColor())
        self._panel.setIgnoresMouseEvents_(True)
        # talkie is never the active app while this matters, so the default
        # "hide when my app deactivates" would hide it exactly when it is needed.
        self._panel.setHidesOnDeactivate_(False)
        self._panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorFullScreenAuxiliary
        )

        effect = NSVisualEffectView.alloc().initWithFrame_(
            NSMakeRect(0, 0, frame.size.width, frame.size.height)
        )
        effect.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        effect.setState_(NSVisualEffectStateActive)
        effect.setWantsLayer_(True)
        effect.layer().setCornerRadius_(CORNER_RADIUS)
        effect.layer().setMasksToBounds_(True)

        self._label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(PADDING, PADDING, frame.size.width - 2 * PADDING,
                       frame.size.height - 2 * PADDING)
        )
        self._label.setStringValue_(LISTENING)
        self._label.setFont_(NSFont.systemFontOfSize_(FONT_SIZE))
        self._label.setTextColor_(NSColor.labelColor())
        self._label.setBezeled_(False)
        self._label.setDrawsBackground_(False)
        self._label.setEditable_(False)
        self._label.setSelectable_(False)
        effect.addSubview_(self._label)
        self._panel.setContentView_(effect)

        self._visible = False
        # A finished transcript is on screen and fading. `settle()` leaves it
        # alone; anything else would take it down before it could be read.
        self._finishing = False
        # Bumped on every show; a queued hide that does not match is stale,
        # because the next dictation started before the last one faded.
        self._token = 0

    # -- from any thread ---------------------------------------------------

    def show(self, text: str = "", state: str = HOLDING) -> None:
        AppHelper.callAfter(self._apply_show, caption(text, state))

    def update(self, text: str) -> None:
        """A partial. Cheap enough to send for every delta."""
        AppHelper.callAfter(self._apply_text, caption(text))

    def finish(self, text: str) -> None:
        """Show the final transcript, then fade out on its own.

        An empty one is not left blank: a clip the model heard nothing in says
        so, rather than looking like the strip failed to update.
        """
        AppHelper.callAfter(self._apply_finish, caption(text, DONE))

    def settle(self) -> None:
        """The dictation ended. Take the strip down — unless a transcript is
        already on screen and still fading, which is the usual case."""
        AppHelper.callAfter(self._apply_settle, None)

    def hide(self) -> None:
        AppHelper.callAfter(self._apply_hide, None)

    # -- main thread only --------------------------------------------------

    def _apply_show(self, text: str) -> None:
        self._token += 1
        self._finishing = False
        self._label.setStringValue_(text)
        self._reposition()
        # Never makeKeyAndOrderFront_: that would activate talkie and the
        # paste would land in this window instead of the user's app.
        self._panel.orderFrontRegardless()
        self._visible = True

    def _apply_text(self, text: str) -> None:
        if self._visible:
            self._label.setStringValue_(text)

    def _apply_finish(self, text: str) -> None:
        self._token += 1
        self._finishing = True
        token = self._token
        self._label.setStringValue_(text)
        if not self._visible:
            self._panel.orderFrontRegardless()
            self._visible = True
        AppHelper.callLater(LINGER, self._expire, token)

    def _expire(self, token: int) -> None:
        if token == self._token:
            self._apply_hide(None)

    def _apply_settle(self, _=None) -> None:
        if not self._finishing:
            self._apply_hide()

    def _apply_hide(self, _=None) -> None:
        self._token += 1
        self._finishing = False
        self._panel.orderOut_(None)
        self._visible = False

    def _reposition(self) -> None:
        """Follow the screen the user is on, not the one the panel was born on."""
        screen = NSScreen.mainScreen()
        if screen is None:
            return
        frame = self._panel.frame()
        self._panel.setFrame_display_(
            frame_for(screen, frame.size.width, frame.size.height), False
        )
