"""The name and icon macOS shows for us.

Running from a bare interpreter, the dock says "python3" and shows the generic
Python icon, because that is genuinely what is running — the name and icon
normally come from an .app bundle's Info.plist, and there is no bundle here.

Both are still settable at runtime:

    icon    NSApplication.setApplicationIconImage_ replaces the dock icon
    name    CFBundleName in the main bundle's info dictionary feeds the
            application menu ("About talkie", "Quit talkie")

The name has to be set before pywebview builds the menu during start(); the
icon can go in at the same time. pywebview does the same trick on the same
dictionary for its own keys, so the dictionary really is mutable.

This stops mattering once talkie ships as an .app (SPEC 5.7) — the bundle will
carry both. Until then the drawing is done in code so there is no binary asset
to keep in sync with it.
"""

from __future__ import annotations

import logging

from AppKit import (
    NSApplication,
    NSBezierPath,
    NSColor,
    NSGradient,
    NSImage,
    NSLineCapStyleRound,
)
from Foundation import NSBundle, NSMakePoint, NSMakeRect, NSMakeSize

log = logging.getLogger(__name__)

APP_NAME = "talkie"
SIZE = 1024.0
INSET = SIZE * 0.09
CORNER = 0.2237  # Apple's squircle, as a fraction of the artwork's width

# The accent blue from the history window's stylesheet, light to dark.
TOP = (0x3B / 255, 0x82 / 255, 0xF6 / 255)
BOTTOM = (0x1E / 255, 0x3A / 255, 0x8A / 255)


def _color(rgb) -> NSColor:
    return NSColor.colorWithSRGBRed_green_blue_alpha_(*rgb, 1.0)


def _draw_mic(cx: float, cy: float, h: float) -> None:
    """A condenser mic: filled capsule, stroked cradle, stem, and base.

    cy is the *base* of the stand, not the glyph's centre — the glyph runs from
    cy - 0.22h to cy + 0.72h, so callers position it with _MIC_OFFSET.
    """
    cap_w, cap_h = h * 0.40, h * 0.62
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(cx - cap_w / 2, cy + h * 0.10, cap_w, cap_h), cap_w / 2, cap_w / 2
    ).fill()

    width, radius = h * 0.085, h * 0.36

    def stroke(build) -> None:
        path = NSBezierPath.bezierPath()
        path.setLineWidth_(width)
        path.setLineCapStyle_(NSLineCapStyleRound)
        build(path)
        path.stroke()

    stroke(
        lambda p: p.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_(
            NSMakePoint(cx, cy + h * 0.30), radius, 200, 340
        )
    )
    stroke(
        lambda p: (
            p.moveToPoint_(NSMakePoint(cx, cy + h * 0.30 - radius)),
            p.lineToPoint_(NSMakePoint(cx, cy - h * 0.22)),
        )
    )
    stroke(
        lambda p: (
            p.moveToPoint_(NSMakePoint(cx - h * 0.24, cy - h * 0.22)),
            p.lineToPoint_(NSMakePoint(cx + h * 0.24, cy - h * 0.22)),
        )
    )


MIC_HEIGHT = SIZE * 0.52
# Centre the glyph in the frame given how lopsided it is around cy.
MIC_BASE = SIZE * 0.5 - 0.25 * MIC_HEIGHT


def icon() -> NSImage:
    """A white mic on the accent-blue squircle. Main thread; needs a GUI session."""
    image = NSImage.alloc().initWithSize_(NSMakeSize(SIZE, SIZE))
    image.lockFocus()
    try:
        artwork = NSMakeRect(INSET, INSET, SIZE - 2 * INSET, SIZE - 2 * INSET)
        corner = (SIZE - 2 * INSET) * CORNER
        squircle = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            artwork, corner, corner
        )
        NSGradient.alloc().initWithStartingColor_endingColor_(
            _color(TOP), _color(BOTTOM)
        ).drawInBezierPath_angle_(squircle, -90)

        NSColor.whiteColor().set()
        _draw_mic(SIZE / 2, MIC_BASE, MIC_HEIGHT)
    finally:
        image.unlockFocus()
    return image


def apply(name: str = APP_NAME) -> None:
    """Claim our own name and icon. Main thread, before webview.start()."""
    bundle = NSBundle.mainBundle()
    info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
    try:
        info["CFBundleName"] = name
    except Exception:
        # A real .app bundle may hand back an immutable dictionary; it would
        # already carry the right name, so there is nothing to fix.
        log.debug("could not set CFBundleName", exc_info=True)

    try:
        NSApplication.sharedApplication().setApplicationIconImage_(icon())
    except Exception:
        log.exception("could not set the dock icon")
