"""
All user-tunable constants for Whisper Dictation. Keep this module lightweight —
it is imported by dictation_logging, which runs before any heavy backend import.
"""
from pathlib import Path

import Quartz

# ── Backend / model ───────────────────────────────────────────────────────────
WHISPER_MODEL_DIR = Path.home() / "App Dev" / "whisper-models"
MODEL_SIZE = "small"
TRANSCRIPTION_BACKEND = "mlx"
MLX_MODEL_REPO = f"mlx-community/whisper-{MODEL_SIZE}-mlx"

# ── Audio ─────────────────────────────────────────────────────────────────────
SAMPLE_RATE = 16000
MIN_DURATION_SEC = 0.5
SILENCE_RMS_THRESHOLD = 0.004
WARMUP_AUDIO_SEC = 1.0

# ── Recording reliability ─────────────────────────────────────────────────────
RECORDING_STATE_POLL_SEC = 0.2
MAX_RECORDING_DURATION_SEC = 120.0
EMPTY_RECORDING_WINDOW_SEC = 30.0
EMPTY_RECORDING_THRESHOLD = 3
# If sounddevice's stream.stop()/close() hangs longer than this, assume the
# CoreAudio mutex deadlock and trigger a process restart instead of leaking
# zombie audio threads forever.
STREAM_CLOSE_WATCHDOG_SEC = 5.0

# ── External transcription guard ──────────────────────────────────────────────
EXTERNAL_TRANSCRIPTION_POLL_SEC = 2.0
EXTERNAL_TRANSCRIPTION_PATTERNS = (
    "whisper",
    "macwhisper",
    "mlx_whisper",
    "faster_whisper",
    "transcribe",
    "transcription",
)

# ── Paste timing ──────────────────────────────────────────────────────────────
PASTE_SHORT_DELAY_SEC = 0.05
PASTE_RESTORE_DELAY_SEC = 0.1
CLAUDE_PASTE_DELAY_SEC = 0.2

# ── Auto-space between consecutive dictations ────────────────────────────────
# When the user dictates twice in a row in the same app, prepend a space so the
# second clip doesn't run together with the first. The heuristic uses elapsed
# time + frontmost-app match; it's deliberately not perfect (see comments in
# Recorder.transcribe_and_type).
AUTO_SPACE_WINDOW_SEC = 30.0

# ── Hotkeys ───────────────────────────────────────────────────────────────────
# Middle-mouse events are intentionally swallowed by the event tap so the click
# does not reach the focused app and move the cursor. See dictation_hotkey.py.
DICTATION_MOUSE_BUTTON = Quartz.kCGMouseButtonCenter
DICTATION_MOUSE_DISPLAY_NAME = "middle mouse button"
FN_FLAG = Quartz.kCGEventFlagMaskSecondaryFn

# ── Launchd ───────────────────────────────────────────────────────────────────
LAUNCH_AGENT_LABEL = "com.example.dictation"

# ── Paths ─────────────────────────────────────────────────────────────────────
# Sibling to dictation.py — using __file__ here keeps the path stable across the
# split since all dictation_*.py files live in the same directory.
LOG_FILE = Path(__file__).parent / "dictation.log"
