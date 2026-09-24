"""Windowed build of talkie: hotkey loop, history, and the window, in one process.

A regular app — dock icon, Cmd-Tab entry, window at launch — that also keeps a
menu-bar item, because the recording indicator has to be visible while you are
in another app and talkie's window is behind it.

Thread layout, which the spikes in DESIGN 4 pinned down:

    main         webview.start() — the single NSApplication run loop
    listener     pynput chord detection
    worker       one per clip: transcribe, paste, write history

Everything that touches AppKit is bounced onto the main thread by MenuBar.
"""

from __future__ import annotations

import logging
import signal

import webview
from AppKit import NSApplication
from PyObjCTools import AppHelper

from talkie.app import ERROR, IDLE, RECORDING, TRANSCRIBING, Talkie
from talkie.config import Config
from talkie.history import History, Interaction
from talkie.permissions import ACCESSIBILITY_HINT, accessibility_trusted
from talkie.settings import Settings, SettingsStore
from talkie.ui import branding, dock
from talkie.ui.api import Api
from talkie.ui.menubar import MenuBar
from talkie.ui.overlay import Overlay
from talkie.ui.window import HistoryWindow

log = logging.getLogger(__name__)


class TalkieApp:
    """Wires the pieces together and owns the process lifetime."""

    def __init__(
        self,
        config: Config,
        history: History | None = None,
        settings: Settings | None = None,
        store: SettingsStore | None = None,
    ) -> None:
        # `config` already carries the saved settings — cli.py resolves them
        # before constructing anything, so the client, the history cap and the
        # menu bar all start from the same numbers.
        self.config = config
        self.settings = settings or Settings.from_config(config)
        self.history = history or History(keep=config.history_keep)
        self.api = Api(
            self.history,
            settings=self.settings,
            store=store,
            on_settings_changed=self._on_settings,
        )
        self.window = HistoryWindow(self.api)
        self.menubar: MenuBar | None = None
        # Built in run(), on the main thread, before the loop starts.
        self.overlay: Overlay | None = None
        self._delegate = None  # AppKit does not retain the app delegate
        self.talkie = Talkie(
            config,
            history=self.history,
            on_state=self._on_state,
            on_record=self._on_record,
            on_partial=self._on_partial,
        )
        self._stopping = False

    # -- observers (called from the worker thread) -------------------------

    def show_history(self) -> None:
        """Bring the window forward — from the menu bar, or a dock-icon click."""
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self.window.show()

    def _on_state(self, state: str) -> None:
        if self.menubar is not None:
            self.menubar.set_state(state)
        if self.overlay is None:
            return
        if state == RECORDING:
            self.overlay.show()
        elif state == TRANSCRIBING and not self.config.streaming:
            # One-shot has nothing to show yet, but the strip staying up says
            # the hotkey was heard — the feature is not conditional on paying
            # for streaming (SPEC §6.4).
            self.overlay.show(state="working")
        elif state in (IDLE, ERROR):
            # A tap too short to transcribe writes no history row, so this is
            # the only thing that takes the strip down again. A clip that did
            # produce one is already fading and `settle` leaves it alone.
            self.overlay.settle()

    def _on_partial(self, text: str) -> None:
        """Every delta, straight off the socket reader thread."""
        if self.overlay is not None:
            self.overlay.update(text)

    def _on_record(self, entry: Interaction) -> None:
        if self.menubar is not None:
            self.menubar.set_stats(self.api.stats())
        if self.overlay is not None:
            # The history row is the one place that knows how it ended, for
            # both modes and for a failure.
            self.overlay.finish(entry.text or (entry.error or ""))
        self.window.refresh()

    def _on_settings(self, settings: Settings) -> None:
        """The settings page saved. Runs on the bridge thread, not the main one.

        Talkie.apply only rebinds attributes, and MenuBar marshals its own
        updates onto the main thread, so nothing here needs a callAfter.
        """
        self.settings = settings
        self.config = settings.apply_to(self.config)
        self.talkie.apply(self.config)
        if self.menubar is not None:
            self.menubar.set_model(self.config.model, self.config.language)

    # -- lifecycle ---------------------------------------------------------

    def run(self) -> None:
        # Before any thread exists. Threads inherit the mask at creation, and
        # CPython only writes the signal wakeup byte when the signal lands on
        # the *main* thread — so every other thread must refuse it.
        self._block_signals_in_future_threads()
        log.info("hotkey %s · model %s", self.talkie.chord, self.config.model)
        if not accessibility_trusted():
            log.warning("Accessibility is not granted — %s", ACCESSIBILITY_HINT)

        self.history.prune()
        # Before the window: pywebview reads CFBundleName when it builds the
        # application menu during start().
        branding.apply()
        self.window.create(hidden=False)
        self.menubar = MenuBar(
            hotkey=str(self.talkie.chord),
            model=self.config.model,
            language=self.config.language,
            on_open_history=self.show_history,
            on_quit=self.stop,
        )
        self.menubar.set_state(IDLE)
        self.menubar.set_stats(self.api.stats())
        self.overlay = Overlay()

        self.talkie.listener.start()
        # After the window exists: callAfter queued before there is anything to
        # drive the run loop does not survive to fire.
        self._watch_signals()
        # After start() has installed pywebview's own delegate, so ours wins.
        AppHelper.callAfter(self._become_regular)

        log.info("running — the talkie window is open, and 🎙 is in the menu bar")
        log.info("quit with Cmd+Q, Ctrl+C, or the menu bar's Quit item")
        try:
            # Blocks on the main thread until the window is destroyed by stop().
            webview.start()
        finally:
            self.talkie.close()  # logs its own goodbye

    def _become_regular(self) -> None:
        """Main thread, inside the run loop. Claims the dock icon and Cmd-Q."""
        self._delegate = dock.install(on_reopen=self.show_history, on_quit=self.stop)

    SIGNALS = (signal.SIGINT, signal.SIGTERM)
    PUMP_INTERVAL = 0.25

    def _block_signals_in_future_threads(self) -> None:
        """Force delivery onto the main thread.

        CPython only trips its handler when a signal lands on the main thread;
        delivered anywhere else it is silently dropped. Called before any
        thread exists, since threads inherit the mask at creation.
        """
        try:
            signal.pthread_sigmask(signal.SIG_BLOCK, set(self.SIGNALS))
        except (AttributeError, ValueError):
            log.debug("pthread_sigmask unavailable", exc_info=True)

    def _watch_signals(self) -> None:
        """Make Ctrl+C work.

        Cocoa's run loop executes no Python bytecode, so a pending signal
        handler never gets a turn — the interpreter has been told to run it
        but never reaches an instruction boundary to do so. A cheap repeating
        timer on the main thread is the fix: each tick is Python bytecode, so
        any pending handler fires on the next one.

        Ordering matters. _block_signals_in_future_threads runs first so the
        signal cannot be delivered to (and dropped by) a worker; the main
        thread is unblocked again here, once AppKit has finished resetting
        masks during startup.
        """
        try:
            for sig in self.SIGNALS:
                signal.signal(sig, self._on_signal)
        except (AttributeError, ValueError, OSError):
            log.debug("signal handling unavailable", exc_info=True)
            return
        AppHelper.callAfter(self._start_pump)

    def _start_pump(self) -> None:
        """Main thread, inside the run loop."""
        try:
            signal.pthread_sigmask(signal.SIG_UNBLOCK, set(self.SIGNALS))
        except (AttributeError, ValueError):
            pass
        self._pump()

    def _pump(self) -> None:
        """Two jobs per tick, both required; proven by spike.

        Re-registering reclaims the OS-level disposition: something under
        Python — WebKit, by elimination — replaces the SIGINT handler after
        startup, so `signal.signal()` called once before the loop is silently
        undone. `getsignal()` still reports our handler, which is what makes
        it so confusing to diagnose.

        The tick itself is the second job: it gives CPython an instruction
        boundary on the main thread, which Cocoa's run loop otherwise never
        offers, so a tripped handler actually gets to run.
        """
        if self._stopping:
            return
        try:
            for sig in self.SIGNALS:
                signal.signal(sig, self._on_signal)
        except (ValueError, OSError):
            pass
        AppHelper.callLater(self.PUMP_INTERVAL, self._pump)

    def _on_signal(self, *_) -> None:
        log.info("interrupted — shutting down")
        self.stop()

    def stop(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        if self.overlay is not None:
            self.overlay.hide()
        self.talkie.listener.stop()
        self.window.destroy()  # unblocks webview.start()
