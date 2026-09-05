"""The runtime name and icon."""

from __future__ import annotations

from Foundation import NSBundle

from talkie.ui import branding


class FakeApp:
    def __init__(self):
        self.icons = []

    def setApplicationIconImage_(self, image):
        self.icons.append(image)


class FakeNSApplication:
    def __init__(self):
        self.app = FakeApp()

    def sharedApplication(self):
        return self.app


def test_the_mic_is_centred_in_the_frame():
    """The glyph is lopsided around its reference point; MIC_BASE corrects for it.

    It runs from base - 0.22h (the stand) to base + 0.72h (the capsule's top),
    so using the frame's centre as the base would ride it high.
    """
    h = branding.MIC_HEIGHT
    top = branding.MIC_BASE + 0.72 * h
    bottom = branding.MIC_BASE - 0.22 * h
    assert (top + bottom) / 2 == branding.SIZE / 2


def test_the_mic_fits_inside_the_squircle():
    h = branding.MIC_HEIGHT
    assert branding.MIC_BASE - 0.22 * h > branding.INSET
    assert branding.MIC_BASE + 0.72 * h < branding.SIZE - branding.INSET


def test_the_icon_is_a_square_at_full_resolution():
    image = branding.icon()
    assert (image.size().width, image.size().height) == (branding.SIZE, branding.SIZE)


def test_apply_names_us_in_the_application_menu(monkeypatch):
    """pywebview reads CFBundleName to build 'About talkie' and 'Quit talkie'."""
    fake = FakeNSApplication()
    monkeypatch.setattr(branding, "NSApplication", fake)

    branding.apply()

    info = NSBundle.mainBundle().infoDictionary()
    assert info["CFBundleName"] == "talkie"
    assert len(fake.app.icons) == 1


def test_a_failing_icon_does_not_stop_startup(monkeypatch, caplog):
    """Branding is cosmetic; it must never be the reason the app won't launch."""
    monkeypatch.setattr(
        branding, "icon", lambda: (_ for _ in ()).throw(RuntimeError("no display"))
    )
    monkeypatch.setattr(branding, "NSApplication", FakeNSApplication())

    branding.apply()  # does not raise

    assert "dock icon" in caplog.text
