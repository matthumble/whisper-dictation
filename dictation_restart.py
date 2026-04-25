"""
Process restart via launchd kickstart, with a manual subprocess relaunch as
fallback. Called both from the menu's "Restart Dictation" item and from the
self-heal trigger when consecutive empty recordings are detected.
"""
import logging
import os
import subprocess
import sys
import time

import rumps
from rumps import rumps as rumps_runtime

from dictation_config import LAUNCH_AGENT_LABEL

log = logging.getLogger(__name__)


def restart_process(recorder):
    log.info("Restarting Whisper Dictation process.")
    recorder.set_menu_state(recorder.app.set_restarting)
    time.sleep(0.15)

    # Prefer the label launchd actually used to spawn us (XPC_SERVICE_NAME),
    # so kickstart always targets the right job even if LAUNCH_AGENT_LABEL
    # drifts from what's installed in ~/Library/LaunchAgents.
    xpc_label = os.environ.get("XPC_SERVICE_NAME")
    launch_label = (
        os.environ.get("LAUNCH_JOB_LABEL") or xpc_label or LAUNCH_AGENT_LABEL
    )
    launchctl_target = f"gui/{os.getuid()}/{launch_label}"
    launchd_supervised = bool(xpc_label)

    try:
        subprocess.run(
            ["launchctl", "kickstart", "-k", launchctl_target],
            check=True,
            capture_output=True,
            text=True,
        )
        log.info("Requested launchd restart for %s.", launchctl_target)
        return
    except Exception:
        log.exception("Launchd restart failed.")

    # If launchd is supervising us (KeepAlive=true), DO NOT also Popen a
    # replacement — quitting alone is enough; launchd will respawn us. The
    # manual relaunch path here would create a second long-lived process.
    if launchd_supervised:
        log.warning("Launchd-supervised; quitting and letting launchd respawn.")
        rumps_runtime.AppHelper.callAfter(rumps.quit_application)
        return

    try:
        subprocess.Popen(
            [sys.executable, *sys.argv],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log.info("Spawned replacement dictation process.")
    except Exception:
        log.exception("Manual relaunch failed.")
        recorder.set_idle_or_external()
        return

    rumps_runtime.AppHelper.callAfter(rumps.quit_application)
