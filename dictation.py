#!/usr/bin/env python3
"""
Whisper Dictation
Hold fn key OR middle mouse button to record, release to transcribe and type.
Menu bar icon shows current state: 🎤 idle, 🔴 recording, ⏳ transcribing.
"""
import logging
import subprocess
import threading
import time
from pathlib import Path

import numpy as np
import pyautogui
import rumps
import sounddevice as sd
import whisper
import Quartz
from pynput import mouse

# ── Config ────────────────────────────────────────────────────────────────────
WHISPER_MODEL_DIR = Path.home() / "App Dev" / "whisper-models"
MODEL_SIZE = "small"
SAMPLE_RATE = 16000
MIN_DURATION_SEC = 0.5
LOG_FILE = Path(__file__).parent / "dictation.log"

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

# ── Menu bar app ──────────────────────────────────────────────────────────────
class DictationApp(rumps.App):
    def __init__(self):
        super().__init__("🎤", quit_button="Quit Dictation")

    def set_idle(self):
        self.title = "🎤"

    def set_recording(self):
        self.title = "🔴"

    def set_transcribing(self):
        self.title = "⏳"


app = DictationApp()

# ── Load model once at startup ────────────────────────────────────────────────
log.info("Loading Whisper '%s' model...", MODEL_SIZE)
model = whisper.load_model(MODEL_SIZE, download_root=str(WHISPER_MODEL_DIR))
log.info("Model ready.")

# ── State ─────────────────────────────────────────────────────────────────────
_recording = False
_audio_chunks: list[np.ndarray] = []
_lock = threading.Lock()


# ── Audio ─────────────────────────────────────────────────────────────────────
def _audio_callback(indata, frames, time_info, status):
    if _recording:
        _audio_chunks.append(indata.copy())


# ── Text output via clipboard paste ───────────────────────────────────────────
def _paste_text(text: str):
    original = subprocess.run(["pbpaste"], capture_output=True, text=True).stdout
    proc = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
    proc.communicate(text.encode("utf-8"))
    time.sleep(0.05)
    pyautogui.hotkey("command", "v")
    time.sleep(0.1)
    proc2 = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
    proc2.communicate(original.encode("utf-8"))


# ── Transcription ─────────────────────────────────────────────────────────────
def _transcribe_and_type():
    with _lock:
        chunks = list(_audio_chunks)

    if not chunks:
        app.set_idle()
        return

    audio = np.concatenate(chunks, axis=0).flatten().astype(np.float32)
    duration = len(audio) / SAMPLE_RATE

    if duration < MIN_DURATION_SEC:
        log.info("Clip too short (%.2fs), skipping.", duration)
        app.set_idle()
        return

    app.set_transcribing()
    log.info("Transcribing %.1fs of audio...", duration)
    result = model.transcribe(audio, language="en", fp16=False)
    text = result["text"].strip()

    if text:
        log.info("Result: %s", text)
        _paste_text(text)
    else:
        log.info("No speech detected.")

    app.set_idle()


# ── Shared start/stop ─────────────────────────────────────────────────────────
def _start_recording():
    global _recording, _audio_chunks
    with _lock:
        if _recording:
            return
        _recording = True
        _audio_chunks = []
    log.info("Recording started.")
    app.set_recording()


def _stop_recording():
    global _recording
    with _lock:
        if not _recording:
            return
        _recording = False
    threading.Thread(target=_transcribe_and_type, daemon=True).start()


# ── fn key via Quartz CGEventTap ──────────────────────────────────────────────
FN_FLAG = Quartz.kCGEventFlagMaskSecondaryFn
_fn_pressed = False


def _quartz_callback(proxy, event_type, event, refcon):
    global _fn_pressed
    if event_type == Quartz.kCGEventFlagsChanged:
        flags = Quartz.CGEventGetFlags(event)
        fn_down = bool(flags & FN_FLAG)
        if fn_down and not _fn_pressed:
            _fn_pressed = True
            _start_recording()
        elif not fn_down and _fn_pressed:
            _fn_pressed = False
            _stop_recording()
    return event


def _start_fn_listener():
    tap = Quartz.CGEventTapCreate(
        Quartz.kCGSessionEventTap,
        Quartz.kCGHeadInsertEventTap,
        Quartz.kCGEventTapOptionDefault,
        Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged),
        _quartz_callback,
        None,
    )
    if not tap:
        log.error("Could not create Quartz event tap — check Accessibility permissions.")
        return
    source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
    Quartz.CFRunLoopAddSource(
        Quartz.CFRunLoopGetCurrent(), source, Quartz.kCFRunLoopCommonModes
    )
    Quartz.CGEventTapEnable(tap, True)
    Quartz.CFRunLoopRun()


# ── Middle mouse via pynput ───────────────────────────────────────────────────
def on_mouse_click(x, y, button, pressed):
    if button == mouse.Button.middle:
        if pressed:
            _start_recording()
        else:
            _stop_recording()


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    log.info("Whisper Dictation started. Hold fn or middle mouse to dictate.")

    threading.Thread(target=_start_fn_listener, daemon=True).start()

    mouse_listener = mouse.Listener(on_click=on_mouse_click)
    mouse_listener.start()

    # Hide from Dock — menu bar only
    import AppKit
    AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        callback=_audio_callback,
    ):
        app.run()  # rumps takes over the main thread (required for macOS UI)
