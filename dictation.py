#!/usr/bin/env python3
"""
Whisper Dictation
Hold fn key OR middle mouse button to record, release to transcribe and type.
Menu bar icon shows current state: 🎤 idle, 🔴 recording, ⏳ transcribing.
"""
import logging
import os
import shlex
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
from AppKit import NSApplicationActivationPolicyAccessory
from pynput import mouse
from rumps import rumps as rumps_runtime

# ── Config ────────────────────────────────────────────────────────────────────
WHISPER_MODEL_DIR = Path.home() / "App Dev" / "whisper-models"
MODEL_SIZE = "small"
SAMPLE_RATE = 16000
MIN_DURATION_SEC = 0.5
LOG_FILE = Path(__file__).parent / "dictation.log"
WARMUP_AUDIO_SEC = 1.0
EXTERNAL_TRANSCRIPTION_POLL_SEC = 2.0
EXTERNAL_TRANSCRIPTION_PATTERNS = (
    "whisper",
    "macwhisper",
    "mlx_whisper",
    "faster_whisper",
    "transcribe",
    "transcription",
)

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


app = DictationApp()

# ── Load model once at startup ────────────────────────────────────────────────
log.info("Loading Whisper '%s' model...", MODEL_SIZE)
model = whisper.load_model(MODEL_SIZE, download_root=str(WHISPER_MODEL_DIR))
log.info("Model ready.")

# ── State ─────────────────────────────────────────────────────────────────────
_recording = False
_audio_chunks: list[np.ndarray] = []
_audio_stream: sd.InputStream | None = None
_audio_sample_rate = float(SAMPLE_RATE)
_external_transcription_active = False
_external_transcription_command: str | None = None
_local_transcription_active = False
_lock = threading.Lock()
_transcription_lock = threading.Lock()


# ── Audio ─────────────────────────────────────────────────────────────────────
def _audio_callback(indata, frames, time_info, status):
    if status:
        log.warning("Audio callback status: %s", status)

    with _lock:
        if _recording:
            _audio_chunks.append(indata.copy())


def _set_menu_state(state_setter):
    rumps_runtime.AppHelper.callAfter(state_setter)


def _set_idle_or_external_transcription():
    with _lock:
        show_external_transcription = _external_transcription_active

    if show_external_transcription:
        _set_menu_state(app.set_external_transcription)
    else:
        _set_menu_state(app.set_idle)


def _is_external_transcription_command(command: str) -> bool:
    normalized = command.lower()
    script_path = str(Path(__file__).resolve()).lower()
    if str(os.getpid()) in normalized or "dictation.py" in normalized or script_path in normalized:
        return False

    try:
        tokens = shlex.split(normalized)
    except ValueError:
        tokens = normalized.split()

    basenames = {Path(token).name for token in tokens}
    joined_tokens = " ".join(tokens)

    if {"macwhisper", "mlx_whisper", "faster_whisper"} & basenames:
        return True
    if "whisper" in basenames:
        return True
    if "sales_agent.transcription" in joined_tokens:
        return True
    if any(f"-m {pattern}" in joined_tokens for pattern in ("transcription", "transcribe")):
        return True

    return False


def _detect_external_transcription() -> tuple[bool, str | None]:
    result = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        capture_output=True,
        text=True,
        check=True,
    )
    current_pid = os.getpid()

    for line in result.stdout.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) != 2:
            continue

        pid, command = parts
        if not pid.isdigit() or int(pid) == current_pid:
            continue
        if _is_external_transcription_command(command):
            return True, command

    return False, None


def _set_external_transcription_status(active: bool, command: str | None):
    global _external_transcription_active, _external_transcription_command

    with _lock:
        was_active = _external_transcription_active
        _external_transcription_active = active
        _external_transcription_command = command
        can_update_menu = not _recording and not _local_transcription_active

    if active and not was_active:
        log.info("External transcription detected; dictation unavailable: %s", command)
    elif not active and was_active:
        log.info("External transcription finished; dictation available.")

    if can_update_menu:
        _set_idle_or_external_transcription()


def _monitor_external_transcription():
    while True:
        try:
            active, command = _detect_external_transcription()
            _set_external_transcription_status(active, command)
        except Exception:
            log.exception("Could not check for external transcription processes.")
        time.sleep(EXTERNAL_TRANSCRIPTION_POLL_SEC)


def _resample_audio(audio: np.ndarray, source_sample_rate: float, target_sample_rate: int) -> np.ndarray:
    if not audio.size or int(source_sample_rate) == int(target_sample_rate):
        return audio.astype(np.float32, copy=False)

    duration = len(audio) / source_sample_rate
    target_length = max(1, int(round(duration * target_sample_rate)))
    source_positions = np.linspace(0, len(audio) - 1, num=len(audio), dtype=np.float64)
    target_positions = np.linspace(0, len(audio) - 1, num=target_length, dtype=np.float64)
    resampled = np.interp(target_positions, source_positions, audio)
    return resampled.astype(np.float32, copy=False)


def _candidate_input_stream_configs() -> list[tuple[int | None, float, str]]:
    candidates: list[tuple[int | None, float, str]] = []
    seen: set[tuple[int | None, int]] = set()
    default_input_device = sd.default.device[0]
    devices = sd.query_devices()

    def add_candidate(device_id: int | None, sample_rate: float, label: str):
        key = (device_id, int(round(sample_rate)))
        if sample_rate <= 0 or key in seen:
            return
        seen.add(key)
        candidates.append((device_id, float(sample_rate), label))

    if default_input_device is not None and default_input_device >= 0:
        device_info = devices[default_input_device]
        add_candidate(
            default_input_device,
            float(device_info["default_samplerate"]),
            f"default input {device_info['name']}",
        )
        add_candidate(default_input_device, SAMPLE_RATE, f"default input {device_info['name']}")

    for fallback_rate in (48000, 44100):
        add_candidate(default_input_device, fallback_rate, "default input fallback")

    for device_id, device_info in enumerate(devices):
        if not device_info["max_input_channels"]:
            continue
        add_candidate(
            device_id,
            float(device_info["default_samplerate"]),
            f"fallback input {device_info['name']}",
        )

    return candidates


def _open_audio_input_stream() -> tuple[sd.InputStream, float]:
    failures: list[str] = []
    for device_id, sample_rate, label in _candidate_input_stream_configs():
        try:
            stream = sd.InputStream(
                device=device_id,
                samplerate=sample_rate,
                channels=1,
                dtype="float32",
                callback=_audio_callback,
            )
            stream.start()
            log.info("Opened audio input using %s at %.0f Hz.", label, sample_rate)
            return stream, sample_rate
        except Exception as exc:
            failures.append(f"{label} @ {sample_rate:.0f} Hz: {exc}")

    raise RuntimeError(" ; ".join(failures) if failures else "No input devices available")


# ── Text output via clipboard paste ───────────────────────────────────────────
def _paste_text(text: str):
    original = subprocess.run(["pbpaste"], capture_output=True, text=True).stdout
    try:
        subprocess.run(["pbcopy"], input=text, text=True, check=True)
        time.sleep(0.05)
        pyautogui.hotkey("command", "v")
        time.sleep(0.1)
    finally:
        subprocess.run(["pbcopy"], input=original, text=True, check=False)


def _warm_up_model():
    try:
        silence = np.zeros(int(SAMPLE_RATE * WARMUP_AUDIO_SEC), dtype=np.float32)
        with _transcription_lock:
            model.transcribe(silence, language="en", fp16=False)
        log.info("Warmup transcription complete.")
    except Exception:
        log.exception("Warmup transcription failed.")


# ── Transcription ─────────────────────────────────────────────────────────────
def _transcribe_and_type():
    global _local_transcription_active
    with _lock:
        chunks = list(_audio_chunks)
        capture_sample_rate = _audio_sample_rate

    if not chunks:
        log.info("No audio captured, skipping transcription.")
        _set_idle_or_external_transcription()
        return

    try:
        audio = np.concatenate(chunks, axis=0).flatten().astype(np.float32)
        duration = len(audio) / capture_sample_rate

        if duration < MIN_DURATION_SEC:
            log.info("Clip too short (%.2fs), skipping.", duration)
            return

        with _lock:
            _local_transcription_active = True
        _set_menu_state(app.set_transcribing)
        log.info("Transcribing %.1fs of audio...", duration)
        audio = _resample_audio(audio, capture_sample_rate, SAMPLE_RATE)
        with _transcription_lock:
            result = model.transcribe(audio, language="en", fp16=False)
        text = result["text"].strip()

        if text:
            log.info("Result: %s", text)
            _paste_text(text)
        else:
            log.info("No speech detected.")
    except Exception:
        log.exception("Transcription failed.")
    finally:
        with _lock:
            _local_transcription_active = False
        _set_idle_or_external_transcription()


# ── Shared start/stop ─────────────────────────────────────────────────────────
def _start_recording():
    global _recording, _audio_chunks, _audio_stream, _audio_sample_rate
    try:
        external_active, command = _detect_external_transcription()
        _set_external_transcription_status(external_active, command)
    except Exception:
        log.exception("Could not check external transcription state before recording.")
        external_active = False

    if external_active:
        log.info("Dictation ignored while external transcription is running.")
        return

    with _lock:
        if _external_transcription_active:
            log.info("Dictation ignored while external transcription is running.")
            return
        if _recording:
            return
        _recording = True
        _audio_chunks = []
        _audio_sample_rate = float(SAMPLE_RATE)

    try:
        stream, capture_sample_rate = _open_audio_input_stream()
    except Exception:
        with _lock:
            _recording = False
            _audio_stream = None
            _audio_sample_rate = float(SAMPLE_RATE)
        log.exception("Could not start audio input stream.")
        _set_idle_or_external_transcription()
        return

    with _lock:
        if not _recording:
            stream.close()
            return
        _audio_stream = stream
        _audio_sample_rate = capture_sample_rate

    log.info("Recording started at %.0f Hz.", capture_sample_rate)
    _set_menu_state(app.set_recording)


def _stop_recording():
    global _recording, _audio_stream, _audio_sample_rate
    with _lock:
        if not _recording:
            return
        _recording = False
        stream = _audio_stream
        _audio_stream = None
        chunk_count = len(_audio_chunks)
        capture_sample_rate = _audio_sample_rate

    if stream:
        try:
            stream.stop()
            stream.close()
        except Exception:
            log.exception("Could not stop audio input stream cleanly.")

    log.info(
        "Recording stopped. Captured %d audio chunks at %.0f Hz.",
        chunk_count,
        capture_sample_rate,
    )
    threading.Thread(target=_transcribe_and_type, daemon=True).start()


# ── fn key via Quartz CGEventTap ──────────────────────────────────────────────
FN_FLAG = Quartz.kCGEventFlagMaskSecondaryFn
_fn_pressed = False


def _quartz_callback(proxy, event_type, event, refcon):
    global _fn_pressed
    if event_type in (
        Quartz.kCGEventTapDisabledByTimeout,
        Quartz.kCGEventTapDisabledByUserInput,
    ):
        Quartz.CGEventTapEnable(proxy, True)
        log.info("Re-enabled fn key event tap after macOS disabled it.")
        return event

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
    threading.Thread(target=_warm_up_model, daemon=True).start()
    threading.Thread(target=_monitor_external_transcription, daemon=True).start()

    mouse_listener = mouse.Listener(on_click=on_mouse_click)
    mouse_listener.start()

    app.run()  # rumps takes over the main thread (required for macOS UI)
