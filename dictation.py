#!/usr/bin/env python3
"""
Whisper Dictation entry point.
Hold fn key OR middle mouse button to record, release to transcribe and type.
Menu bar icon shows current state: 🎤 idle, 🔴 recording, ⏳ transcribing, 📞 external transcription, ↻ restarting.
"""
import dictation_logging  # noqa: F401  — must be first; sets TQDM_DISABLE and root logger

import logging
import threading

from dictation_config import DICTATION_MOUSE_DISPLAY_NAME
from dictation_hotkey import start_listener
from dictation_menu import app, install_menu_callbacks
from dictation_output import paste_text
from dictation_restart import restart_process
from dictation_state import Recorder
from dictation_transcribe import transcribe_audio, warm_up_model

log = logging.getLogger(__name__)


if __name__ == "__main__":
    log.info("Whisper Dictation started. Hold fn or %s to dictate.", DICTATION_MOUSE_DISPLAY_NAME)

    recorder = Recorder(
        app=app,
        transcribe_fn=transcribe_audio,
        paste_fn=paste_text,
        restart_fn=restart_process,
    )
    install_menu_callbacks(recorder)

    threading.Thread(target=lambda: start_listener(recorder), daemon=True).start()
    threading.Thread(target=warm_up_model, daemon=True).start()
    threading.Thread(target=recorder.monitor_external_transcription, daemon=True).start()
    threading.Thread(target=recorder.monitor_recording_state, daemon=True).start()

    app.run()  # rumps takes over the main thread (required for macOS UI)
