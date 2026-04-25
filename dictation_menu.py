"""
Menu bar app + menu commands. The DictationApp.run() override is intentionally
verbose — it duplicates rumps internals so we can switch the activation policy
to Accessory before the status bar is initialized, which keeps Python.app out
of the Dock. Do not "simplify" without verifying the Dock icon stays hidden.
"""
import logging
import threading
import time

import rumps
from AppKit import NSApplicationActivationPolicyAccessory
from rumps import rumps as rumps_runtime

from dictation_output import copy_text_to_clipboard

log = logging.getLogger(__name__)

# Set by install_menu_callbacks() at startup; the @rumps.clicked-decorated
# functions read it at click time, by which point the entry script has run.
_recorder = None


def call_on_main(fn):
    """Schedule fn() on the main thread via rumps' AppHelper."""
    rumps_runtime.AppHelper.callAfter(fn)


class DictationApp(rumps.App):
    def __init__(self):
        super().__init__("🎤", quit_button="Quit Dictation")

    # rumps activates the host app as a normal foreground app by default,
    # which makes macOS surface Python.app in the Dock. Run as an accessory
    # app instead so only the menu bar item is visible.
    def run(self, **options):
        dont_change = object()
        debug = options.get("debug", dont_change)
        if debug is not dont_change:
            rumps.debug_mode(debug)

        nsapplication = rumps_runtime.NSApplication.sharedApplication()
        nsapplication.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        self._nsapp = rumps_runtime.NSApp.alloc().init()
        self._nsapp._app = self.__dict__
        nsapplication.setDelegate_(self._nsapp)
        rumps_runtime.notifications._init_nsapp(self._nsapp)

        setattr(rumps.App, "*app_instance", self)
        for timer_obj in getattr(rumps_runtime.timer, "*timers", []):
            timer_obj.start()
        for button_callback in getattr(rumps_runtime.clicked, "*buttons", []):
            button_callback(self)

        self._nsapp.initializeStatusBar()
        nsapplication.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        rumps_runtime.AppHelper.installMachInterrupt()
        rumps_runtime.events.before_start.emit()
        rumps_runtime.AppHelper.runEventLoop()

    def set_idle(self):
        self.title = "🎤"

    def set_recording(self):
        self.title = "🔴"

    def set_transcribing(self):
        self.title = "⏳"

    def set_external_transcription(self):
        self.title = "📞"

    def set_restarting(self):
        self.title = "↻"


app = DictationApp()


# ── Recording-duration timer ──────────────────────────────────────────────────
# Updates the menu title (e.g. "🔴 0:12") every 0.5s while recording. Owned
# here because rumps.Timer is NSTimer-backed and must run on the main thread.
_recording_timer = None
_recording_started_at = [0.0]
_recording_timer_running = [False]


def _tick_recording_title(_sender):
    started = _recording_started_at[0]
    if started <= 0:
        return
    elapsed = max(0.0, time.time() - started)
    minutes = int(elapsed // 60)
    seconds = int(elapsed % 60)
    app.title = f"🔴 {minutes}:{seconds:02d}"


def start_recording_timer(started_at: float):
    """Must be invoked on the main thread (via call_on_main)."""
    global _recording_timer
    _recording_started_at[0] = started_at
    if _recording_timer is None:
        _recording_timer = rumps.Timer(_tick_recording_title, 0.5)
    if not _recording_timer_running[0]:
        _recording_timer.start()
        _recording_timer_running[0] = True


def stop_recording_timer():
    """Must be invoked on the main thread (via call_on_main)."""
    _recording_started_at[0] = 0.0
    if _recording_timer is not None and _recording_timer_running[0]:
        _recording_timer.stop()
        _recording_timer_running[0] = False


def install_menu_callbacks(recorder):
    global _recorder
    _recorder = recorder


@rumps.clicked("Restart Dictation")
def _restart_clicked(_sender):
    if _recorder is None:
        return
    threading.Thread(target=_recorder.restart, daemon=True).start()


@rumps.clicked("Copy Last Dictation")
def _copy_last_clicked(_sender):
    if _recorder is None:
        return
    text = _recorder.last_dictation_text
    if not text:
        log.info("Copy Last Dictation requested, but no transcript is available yet.")
        return
    try:
        copy_text_to_clipboard(text)
        log.info("Copied last dictation to clipboard.")
    except Exception:
        log.exception("Could not copy last dictation to clipboard.")
