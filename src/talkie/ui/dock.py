"""Regular-app behaviour: the dock icon and Cmd-Q.

talkie runs as a regular app (dock icon, Cmd-Tab entry, window at launch), so
macOS sends it two messages a menu-bar accessory never sees:

    reopen      the dock icon was clicked while no window is on screen
    terminate   Cmd-Q, or Quit from the dock icon's context menu

pywebview installs its own NSApplication delegate and answers neither the way
we need, so we replace it once the run loop is up. Its delegate does only two
things — one hands Cmd-Q to the window's `closing` handlers, which talkie vetoes
to keep the run loop alive, so Cmd-Q would silently do nothing; the other is a
constant. Both are reproduced here rather than forwarded, so nothing depends on
pywebview's private BrowserView.

Every quit route therefore lands on the same shutdown path: the menu item,
Cmd-Q, the dock menu, and Ctrl+C.
"""

from __future__ import annotations

import logging

import objc
from AppKit import NSApplication, NSApplicationActivationPolicyRegular
from Foundation import NSObject
from PyObjCTools import AppHelper

log = logging.getLogger(__name__)

NS_TERMINATE_CANCEL = 0


class _Delegate(NSObject):
    """NSApplicationDelegate. Held by install()'s caller; AppKit does not retain it."""

    def initWithHandlers_(self, handlers):
        self = objc.super(_Delegate, self).init()
        if self is None:
            return None
        self._handlers = handlers
        return self

    def applicationShouldHandleReopen_hasVisibleWindows_(self, app, has_visible):
        """Dock icon clicked. Our window hides rather than closes, so re-show it."""
        if not has_visible:
            self.dispatch("reopen")
        return True

    def applicationShouldTerminate_(self, app):
        """Shut down our way instead of AppKit's.

        Returning "now" would exec exit() and never unwind Python, skipping the
        listener and recorder teardown. Cancel, and let the handler destroy the
        window — that returns webview.start() and the process ends normally.
        """
        self.dispatch("quit")
        return NS_TERMINATE_CANCEL

    def applicationSupportsSecureRestorableState_(self, app):
        return True

    @objc.python_method
    def dispatch(self, name):
        # Deferred: re-entering AppKit from inside its own delegate callback
        # (destroying a window mid-terminate) is asking for trouble.
        AppHelper.callAfter(self._run, name)

    @objc.python_method
    def _run(self, name):
        try:
            self._handlers[name]()
        except Exception:
            log.exception("%s handler failed", name)


def install(on_reopen, on_quit) -> object:
    """Become a regular app. Main thread, once the run loop is going.

    pywebview already sets the Regular policy, but it is set here too so this
    does not depend on that staying true. Returns the delegate; keep the
    reference alive for the life of the app.
    """
    delegate = _Delegate.alloc().initWithHandlers_({"reopen": on_reopen, "quit": on_quit})
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    app.setDelegate_(delegate)
    return delegate
