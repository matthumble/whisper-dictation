"""
Optional auto-pause for Now Playing media (Spotify / Music / Podcasts /
Overcast / browser players that respect macOS media keys) while dictation
is active.

Pause/play commands go through MRMediaRemoteSendCommand from Apple's
private MediaRemote framework. To decide whether to act, we probe macOS
power-management assertions via `pmset -g assertions` — apps using
CoreAudio for media playback raise a "MediaPlayback" / "CoreMedia
Playback" assertion that disappears when playback actually stops. This
covers any app that uses the system audio stack (Apple's Podcasts,
Music, Spotify, browser-based YouTube, etc.) without needing a private
async API or per-app AppleScript dictionaries.
"""
import ctypes
import logging
import subprocess

log = logging.getLogger(__name__)

_MR_PLAY = 0
_MR_PAUSE = 1

_send_command = None
_load_attempted = False


def _try_load() -> bool:
    global _send_command, _load_attempted
    if _load_attempted:
        return _send_command is not None
    _load_attempted = True
    try:
        lib = ctypes.CDLL(
            "/System/Library/PrivateFrameworks/MediaRemote.framework/MediaRemote"
        )
        send = lib.MRMediaRemoteSendCommand
        send.argtypes = [ctypes.c_int, ctypes.c_void_p]
        send.restype = ctypes.c_bool
        _send_command = send
        log.info("MediaRemote loaded; auto-pause available.")
        return True
    except Exception:
        log.exception(
            "Could not load MediaRemote framework — auto-pause will be a no-op."
        )
        return False


def is_available() -> bool:
    return _try_load()


def is_media_playing() -> bool:
    """Best-effort probe. True if anything is actively routing audio to an
    output device or holding a media-playback assertion. False on any
    error so misdetection fails closed (won't pause what we don't see)."""
    try:
        result = subprocess.run(
            ["pmset", "-g", "assertions"],
            capture_output=True,
            text=True,
            timeout=0.5,
        )
    except Exception:
        log.exception("pmset probe failed; assuming no media playing.")
        return False

    if result.returncode != 0:
        return False

    for line in result.stdout.splitlines():
        # We use only signals known to drop off when the user actually
        # pauses (not just when an audio session is allocated). The
        # `audio-out` resource line is too eager — coreaudiod keeps it
        # alive while a paused app holds the device open for fast resume.
        # - "MediaPlayback" / "CoreMedia Playback": Apple's Podcasts.app
        # - '"Playing audio"': Chromium-based browsers (Chrome, Edge, ...)
        if "CoreMedia Playback" in line or "MediaPlayback" in line:
            return True
        if '"Playing audio"' in line:
            return True
    return False


def pause():
    if not _try_load():
        return
    try:
        _send_command(_MR_PAUSE, None)
    except Exception:
        log.exception("MRMediaRemoteSendCommand(pause) failed.")


def play():
    if not _try_load():
        return
    try:
        _send_command(_MR_PLAY, None)
    except Exception:
        log.exception("MRMediaRemoteSendCommand(play) failed.")
