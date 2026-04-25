"""
Recorder: the central state container. Replaces ~12 module globals with one
locked object. Audio capture, transcription, external-transcription detection,
recording-state polling, and self-heal all live here.

Locking conventions:
- self._lock guards all instance state mutated outside the audio callback path.
  Critical sections must do NO I/O, sleeps, or subprocess calls — the audio
  callback runs on PortAudio's RT thread and contends for this lock.
- self._recent_short_recordings is mutated only inside transcribe_and_type,
  which is serialized by dictation_transcribe's own lock. It is NOT guarded by
  self._lock — wrapping it would risk a nested-lock deadlock with the audio
  callback path.
"""
import logging
import os
import shlex
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np
import Quartz

from dictation_audio import open_input_stream, resample_audio
from dictation_config import (
    AUTO_SPACE_WINDOW_SEC,
    DICTATION_MOUSE_BUTTON,
    EMPTY_RECORDING_THRESHOLD,
    EMPTY_RECORDING_WINDOW_SEC,
    EXTERNAL_TRANSCRIPTION_POLL_SEC,
    FN_FLAG,
    LOG_FILE,
    MAX_RECORDING_DURATION_SEC,
    MIN_DURATION_SEC,
    RECORDING_STATE_POLL_SEC,
    SAMPLE_RATE,
    SILENCE_RMS_THRESHOLD,
)
from dictation_menu import call_on_main, start_recording_timer, stop_recording_timer
from dictation_output import frontmost_app_name

log = logging.getLogger(__name__)


def load_last_dictation_from_log() -> str:
    """Recover the most recent transcript from the log file, for the
    'Copy Last Dictation' menu item to work after a restart."""
    if not LOG_FILE.exists():
        return ""

    marker = b"Result: "
    try:
        with LOG_FILE.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            # Bounded tail read; well within the RotatingFileHandler 1 MB cap.
            f.seek(max(0, size - 262144))
            tail = f.read()
        for line in reversed(tail.split(b"\n")):
            if marker in line:
                return line.split(marker, 1)[1].decode("utf-8", "replace").strip()
    except Exception:
        log.exception("Could not restore last dictation from log.")

    return ""


class Recorder:
    def __init__(self, app, transcribe_fn, paste_fn, restart_fn):
        self.app = app
        self._transcribe = transcribe_fn
        self._paste = paste_fn
        self._restart = restart_fn

        self._lock = threading.Lock()

        # Recording state
        self.recording = False
        self.audio_chunks: list[np.ndarray] = []
        self.audio_stream = None
        self.audio_sample_rate = float(SAMPLE_RATE)
        self.recording_started_at = 0.0

        # External transcription state
        self.external_active = False
        self.external_command: Optional[str] = None

        # Local transcription state
        self.local_transcription_active = False

        # User-visible
        self.last_dictation_text = load_last_dictation_from_log()

        # Self-heal: see locking note in module docstring
        self._recent_short_events: list[float] = []

        # Auto-space heuristic: track when and where we last pasted so a quick
        # follow-up dictation in the same app gets a leading space.
        self._last_paste_at = 0.0  # time.monotonic()
        self._last_paste_app: Optional[str] = None

    # ── Menu state helpers ───────────────────────────────────────────────────
    def set_menu_state(self, state_setter):
        call_on_main(state_setter)

    def set_idle_or_external(self):
        with self._lock:
            show_external = self.external_active

        if show_external:
            call_on_main(self.app.set_external_transcription)
        else:
            call_on_main(self.app.set_idle)

    # ── Audio callback (RT thread) ───────────────────────────────────────────
    def audio_callback(self, indata, frames, time_info, status):
        if status:
            log.warning("Audio callback status: %s", status)
        with self._lock:
            if self.recording:
                self.audio_chunks.append(indata.copy())

    # ── External transcription detection ─────────────────────────────────────
    def _is_external_transcription_command(self, command: str) -> bool:
        normalized = command.lower()
        if str(os.getpid()) in normalized or "dictation.py" in normalized:
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

    def _detect_external_transcription(self) -> tuple[bool, Optional[str]]:
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
            if self._is_external_transcription_command(command):
                return True, command

        return False, None

    def _set_external_status(self, active: bool, command: Optional[str]):
        with self._lock:
            was_active = self.external_active
            self.external_active = active
            self.external_command = command
            can_update_menu = not self.recording and not self.local_transcription_active

        if active and not was_active:
            log.info("External transcription detected; dictation unavailable: %s", command)
        elif not active and was_active:
            log.info("External transcription finished; dictation available.")

        if can_update_menu:
            self.set_idle_or_external()

    def monitor_external_transcription(self):
        while True:
            try:
                active, command = self._detect_external_transcription()
                self._set_external_status(active, command)
            except Exception:
                log.exception("Could not check for external transcription processes.")
            time.sleep(EXTERNAL_TRANSCRIPTION_POLL_SEC)

    # ── Recording-state monitor (release-event safety net) ───────────────────
    def monitor_recording_state(self):
        while True:
            try:
                with self._lock:
                    is_recording = self.recording
                    started_at = self.recording_started_at

                if is_recording:
                    elapsed = max(0.0, time.time() - started_at)
                    if elapsed > MAX_RECORDING_DURATION_SEC:
                        log.warning(
                            "Recording exceeded %.0fs cap; force-stopping after %.1fs.",
                            MAX_RECORDING_DURATION_SEC,
                            elapsed,
                        )
                        self.stop()
                        time.sleep(RECORDING_STATE_POLL_SEC)
                        continue

                    flags = Quartz.CGEventSourceFlagsState(Quartz.kCGEventSourceStateHIDSystemState)
                    fn_down = bool(flags & FN_FLAG)
                    mouse_down = bool(
                        Quartz.CGEventSourceButtonState(
                            Quartz.kCGEventSourceStateHIDSystemState,
                            DICTATION_MOUSE_BUTTON,
                        )
                    )

                    if not fn_down and not mouse_down:
                        log.info(
                            "Input release event appears to have been missed; force-stopping recording after %.1fs.",
                            elapsed,
                        )
                        self.stop()
            except Exception:
                log.exception("Could not reconcile recording state.")

            time.sleep(RECORDING_STATE_POLL_SEC)

    # ── Self-heal trigger ────────────────────────────────────────────────────
    def record_short_event(self):
        now = time.monotonic()
        self._recent_short_events.append(now)
        cutoff = now - EMPTY_RECORDING_WINDOW_SEC
        self._recent_short_events[:] = [t for t in self._recent_short_events if t >= cutoff]
        if len(self._recent_short_events) >= EMPTY_RECORDING_THRESHOLD:
            log.warning(
                "Detected %d empty/silent recordings within %.0fs — self-healing via restart.",
                len(self._recent_short_events),
                EMPTY_RECORDING_WINDOW_SEC,
            )
            self._recent_short_events.clear()
            threading.Thread(target=self.restart, daemon=True).start()

    # ── Restart ──────────────────────────────────────────────────────────────
    def restart(self):
        self._restart(self)

    # ── Start / stop ─────────────────────────────────────────────────────────
    def start(self):
        try:
            external_active, command = self._detect_external_transcription()
            self._set_external_status(external_active, command)
        except Exception:
            log.exception("Could not check external transcription state before recording.")
            external_active = False

        if external_active:
            log.info("Dictation ignored while external transcription is running.")
            return

        with self._lock:
            if self.external_active:
                log.info("Dictation ignored while external transcription is running.")
                return
            if self.recording:
                return
            self.recording = True
            self.audio_chunks = []
            self.audio_sample_rate = float(SAMPLE_RATE)
            self.recording_started_at = time.time()

        try:
            stream, capture_sample_rate = open_input_stream(self.audio_callback)
        except Exception:
            with self._lock:
                self.recording = False
                self.audio_stream = None
                self.audio_sample_rate = float(SAMPLE_RATE)
            log.exception("Could not start audio input stream.")
            self.set_idle_or_external()
            return

        with self._lock:
            if not self.recording:
                stream.close()
                return
            self.audio_stream = stream
            self.audio_sample_rate = capture_sample_rate

        log.info("Recording started at %.0f Hz.", capture_sample_rate)
        started_at = self.recording_started_at
        call_on_main(self.app.set_recording)
        call_on_main(lambda: start_recording_timer(started_at))

    def stop(self):
        with self._lock:
            if not self.recording:
                return
            self.recording = False
            stream = self.audio_stream
            self.audio_stream = None
            chunk_count = len(self.audio_chunks)
            capture_sample_rate = self.audio_sample_rate
            self.recording_started_at = 0.0

        call_on_main(stop_recording_timer)

        if stream:
            # Close on a background thread so a hung stream cannot deadlock the
            # stop path. self.recording=False is already cleared above so the
            # audio callback drops any in-flight frames during the close.
            def _close_stream(s):
                try:
                    s.stop()
                    s.close()
                except Exception:
                    log.exception("Could not stop audio input stream cleanly.")

            threading.Thread(target=_close_stream, args=(stream,), daemon=True).start()

        log.info(
            "Recording stopped. Captured %d audio chunks at %.0f Hz.",
            chunk_count,
            capture_sample_rate,
        )
        threading.Thread(target=self.transcribe_and_type, daemon=True).start()

    # ── Transcribe + paste ───────────────────────────────────────────────────
    def transcribe_and_type(self):
        with self._lock:
            chunks = list(self.audio_chunks)
            capture_sample_rate = self.audio_sample_rate

        if not chunks:
            log.info("No audio captured, skipping transcription.")
            self.record_short_event()
            self.set_idle_or_external()
            return

        try:
            audio = np.concatenate(chunks, axis=0).flatten().astype(np.float32)
            duration = len(audio) / capture_sample_rate

            if duration < MIN_DURATION_SEC:
                log.info("Clip too short (%.2fs), skipping.", duration)
                self.record_short_event()
                return

            # RMS is rate-independent — check before resample. Catches dead-mic
            # captures (CoreAudio fallback bug) and silent clips that previously
            # hallucinated as a stray "V".
            rms = float(np.sqrt(np.mean(np.square(audio))))
            if rms < SILENCE_RMS_THRESHOLD:
                log.info("Silent clip (rms=%.4f, %.2fs); skipping.", rms, duration)
                self.record_short_event()
                return

            with self._lock:
                self.local_transcription_active = True
            call_on_main(self.app.set_transcribing)
            log.info("Transcribing %.1fs of audio...", duration)
            audio = resample_audio(audio, capture_sample_rate, SAMPLE_RATE)
            result = self._transcribe(audio)
            text = result["text"].strip()

            if text:
                self.last_dictation_text = text
                self._recent_short_events.clear()
                log.info("Result: %s", text)

                # Auto-space heuristic: if we pasted recently into this same
                # app and the new text doesn't already start with whitespace,
                # prepend a space so the two clips don't run together.
                # Known false positives (acceptable): user manually deleted the
                # previous paste or moved to a fresh field within the window.
                current_app = frontmost_app_name()
                if (
                    self._last_paste_at
                    and self._last_paste_app
                    and current_app == self._last_paste_app
                    and (time.monotonic() - self._last_paste_at) < AUTO_SPACE_WINDOW_SEC
                    and not text[0].isspace()
                ):
                    text = " " + text

                self._paste(text, frontmost=current_app)
                self._last_paste_at = time.monotonic()
                self._last_paste_app = current_app
            else:
                log.info("No speech detected.")
        except Exception:
            log.exception("Transcription failed.")
        finally:
            with self._lock:
                self.local_transcription_active = False
            self.set_idle_or_external()
