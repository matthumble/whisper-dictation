"""
Persistent user preferences. Single JSON blob stored next to dictation.py,
gitignored. Unknown keys are dropped on load so stale prefs don't accumulate.
"""
import json
import logging
from pathlib import Path

from dictation_config import LOG_FILE

log = logging.getLogger(__name__)

PREFS_FILE = LOG_FILE.parent / "prefs.json"

DEFAULTS: dict = {
    "auto_pause_media": False,
}


def load() -> dict:
    prefs = dict(DEFAULTS)
    if not PREFS_FILE.exists():
        return prefs
    try:
        with PREFS_FILE.open("r") as f:
            stored = json.load(f)
        if isinstance(stored, dict):
            for key in DEFAULTS:
                if key in stored:
                    prefs[key] = stored[key]
    except Exception:
        log.exception("Could not read prefs file %s; using defaults.", PREFS_FILE)
    return prefs


def save(prefs: dict):
    try:
        # Persist only known keys, in a stable shape
        out = {key: prefs.get(key, DEFAULTS[key]) for key in DEFAULTS}
        PREFS_FILE.write_text(json.dumps(out, indent=2) + "\n")
    except Exception:
        log.exception("Could not write prefs file %s", PREFS_FILE)
