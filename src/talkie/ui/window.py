"""The history window.

Shown at launch and toggled from the menu bar thereafter. It is never
destroyed until quit, because webview.start() returns when the last window
goes away — and that return would end the process. Closing the window is
therefore intercepted and turned into a hide. (SPEC 5.3.)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import webview

from talkie.ui.api import Api

log = logging.getLogger(__name__)

WEB_ROOT = Path(__file__).parent / "web"
INDEX = WEB_ROOT / "index.html"

MISSING_UI = """
<style>body{font:14px -apple-system,system-ui;padding:2rem;color:#333}
code{background:#f0f0f0;padding:2px 6px;border-radius:4px}</style>
<h2>The history UI hasn't been built</h2>
<p>Run <code>make web</code> to build the TypeScript frontend, then reopen this window.</p>
"""


class HistoryWindow:
    """Owns the single pywebview window."""

    def __init__(self, api: Api, index: Path = INDEX) -> None:
        self.api = api
        self.index = index
        self.window: webview.Window | None = None

    def create(self, hidden: bool = False) -> webview.Window:
        """Must run on the main thread, before webview.start()."""
        kwargs = dict(
            title="talkie",
            js_api=self.api,
            width=760,
            height=620,
            min_size=(520, 400),
            hidden=hidden,
        )
        if self.index.exists():
            self.window = webview.create_window(url=str(self.index), **kwargs)
        else:
            log.warning("history UI not built at %s", self.index)
            self.window = webview.create_window(html=MISSING_UI, **kwargs)
        self.window.events.closing += self._on_closing
        return self.window

    def _on_closing(self) -> bool:
        """Hide instead of destroying, so the run loop (and menu bar) survive."""
        if self.window is not None:
            self.window.hide()
        return False

    # -- called from any thread -------------------------------------------

    def show(self) -> None:
        if self.window is None:
            return
        try:
            self.window.show()
        except Exception:
            log.exception("could not show the history window")

    def refresh(self) -> None:
        """Tell the page new history landed; harmless if it isn't listening."""
        if self.window is None:
            return
        try:
            self.window.evaluate_js("window.talkie && window.talkie.onHistoryChanged()")
        except Exception:
            log.debug("history refresh skipped (window not ready)", exc_info=True)

    def destroy(self) -> None:
        if self.window is None:
            return
        try:
            self.window.events.closing -= self._on_closing  # let it actually close
            self.window.destroy()
        except Exception:
            log.exception("could not destroy the history window")
