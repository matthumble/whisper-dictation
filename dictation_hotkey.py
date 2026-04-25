"""
Quartz event tap for fn key + middle mouse push-to-talk.

CRITICAL: middle-mouse events return None (event swallowed) so the click does
not propagate to the focused app and move its cursor. This was specifically
wired and is documented in README.md ("Middle-click is captured with Quartz
and swallowed before it reaches the focused app, so it should not move the
text cursor while starting dictation"). Do NOT change return values without
also retesting cursor placement in Claude/Notes/Safari.
"""
import logging

import Quartz

from dictation_config import DICTATION_MOUSE_BUTTON, FN_FLAG

log = logging.getLogger(__name__)


def start_listener(recorder):
    """
    Create the event tap, install it on the current run loop, and run the loop.
    Blocks forever — call from a daemon thread.
    """
    fn_pressed = [False]
    mouse_pressed = [False]

    def callback(proxy, event_type, event, refcon):
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
            if fn_down and not fn_pressed[0]:
                fn_pressed[0] = True
                recorder.start()
            elif not fn_down and fn_pressed[0]:
                fn_pressed[0] = False
                recorder.stop()
            return event

        if event_type in (
            Quartz.kCGEventOtherMouseDown,
            Quartz.kCGEventOtherMouseUp,
            Quartz.kCGEventOtherMouseDragged,
        ):
            button = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGMouseEventButtonNumber)
            if button != DICTATION_MOUSE_BUTTON:
                return event

            if event_type == Quartz.kCGEventOtherMouseDown:
                if not mouse_pressed[0]:
                    mouse_pressed[0] = True
                    recorder.start()
                return None  # swallow — must not reach focused app

            if event_type == Quartz.kCGEventOtherMouseUp and mouse_pressed[0]:
                mouse_pressed[0] = False
                recorder.stop()
                return None  # swallow

            return None  # swallow drags / unmatched up

        return event

    tap = Quartz.CGEventTapCreate(
        Quartz.kCGSessionEventTap,
        Quartz.kCGHeadInsertEventTap,
        Quartz.kCGEventTapOptionDefault,
        (
            Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged)
            | Quartz.CGEventMaskBit(Quartz.kCGEventOtherMouseDown)
            | Quartz.CGEventMaskBit(Quartz.kCGEventOtherMouseUp)
            | Quartz.CGEventMaskBit(Quartz.kCGEventOtherMouseDragged)
        ),
        callback,
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
