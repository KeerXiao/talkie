"""Permission probes, with the pyobjc calls faked."""

import sys
import types

import pytest

from talkie.permissions import accessibility_trusted, prompt_for_accessibility


@pytest.fixture
def fake_appservices(monkeypatch):
    """Stand in for the ApplicationServices framework."""

    def install(trusted):
        module = types.ModuleType("ApplicationServices")
        module.AXIsProcessTrusted = lambda: trusted
        module.AXIsProcessTrustedWithOptions = lambda options: trusted
        module.kAXTrustedCheckOptionPrompt = "AXTrustedCheckOptionPrompt"
        monkeypatch.setitem(sys.modules, "ApplicationServices", module)
        return module

    return install


@pytest.mark.parametrize("trusted", [True, False])
def test_reports_what_macos_says(fake_appservices, trusted):
    fake_appservices(trusted)
    assert accessibility_trusted() is trusted


def test_prompting_returns_the_trust_state(fake_appservices):
    fake_appservices(False)
    assert prompt_for_accessibility() is False


def test_off_macos_there_is_no_gate(monkeypatch):
    """Without pyobjc there is nothing to ask, so don't block the paste."""
    monkeypatch.setitem(sys.modules, "ApplicationServices", None)
    assert accessibility_trusted() is True
    assert prompt_for_accessibility() is True
