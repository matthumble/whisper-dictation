"""
Text output: clipboard copy, paste via cmd-v, frontmost app probe. Paste flow
saves and restores the user's clipboard, with per-app delay tuning.
"""
import logging
import subprocess
import time
from typing import Optional

import Quartz

from dictation_config import (
    CLAUDE_PASTE_DELAY_SEC,
    PASTE_RESTORE_DELAY_SEC,
    PASTE_SHORT_DELAY_SEC,
)

log = logging.getLogger(__name__)

_V_KEYCODE = 9  # ANSI 'v'


def frontmost_app_name() -> Optional[str]:
    try:
        result = subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "System Events" to get name of first application process whose frontmost is true',
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() or None
    except Exception:
        log.exception("Could not determine frontmost application.")
        return None


def _press_cmd_v_via_cgevent():
    src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    down = Quartz.CGEventCreateKeyboardEvent(src, _V_KEYCODE, True)
    up = Quartz.CGEventCreateKeyboardEvent(src, _V_KEYCODE, False)
    Quartz.CGEventSetFlags(down, Quartz.kCGEventFlagMaskCommand)
    Quartz.CGEventSetFlags(up, Quartz.kCGEventFlagMaskCommand)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)


def _send_paste_shortcut() -> bool:
    try:
        subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "System Events" to keystroke "v" using command down',
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return True
    except Exception:
        log.exception("AppleScript paste failed, falling back to CGEvent cmd-v.")
        _press_cmd_v_via_cgevent()
        return False


def copy_text_to_clipboard(text: str):
    subprocess.run(["pbcopy"], input=text, text=True, check=True)


def paste_text(text: str, frontmost: Optional[str] = None):
    """
    Save the user's clipboard, copy `text`, paste via cmd-v, then restore.
    `frontmost` may be provided to skip a redundant osascript probe; if None,
    the frontmost app is queried inside this function.
    """
    original = subprocess.run(["pbpaste"], capture_output=True, text=True).stdout
    try:
        copy_text_to_clipboard(text)
        if frontmost is None:
            frontmost = frontmost_app_name()
        log.info("Pasting into frontmost app: %s", frontmost or "unknown")
        if frontmost == "Claude":
            time.sleep(CLAUDE_PASTE_DELAY_SEC)
        else:
            time.sleep(PASTE_SHORT_DELAY_SEC)
        _send_paste_shortcut()
        time.sleep(PASTE_RESTORE_DELAY_SEC)
    finally:
        subprocess.run(["pbcopy"], input=original, text=True, check=False)
