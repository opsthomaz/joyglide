# SPDX-License-Identifier: GPL-3.0-or-later
"""Global keyboard hotkey via Quartz CGEventTap.

Runs a CFRunLoop on a daemon thread that observes (does not intercept) keyDown
events at the session level. Invokes the registered callback when ⌃⌥M fires.

Requires the Accessibility permission the app already requests for cursor
injection — no extra entitlement.
"""
import threading

from applog import get_logger
from Quartz import (
    CGEventTapCreate,
    CGEventGetFlags,
    CGEventGetIntegerValueField,
    CGEventMaskBit,
    CGEventTapEnable,
    CFMachPortCreateRunLoopSource,
    CFRunLoopAddSource,
    CFRunLoopGetCurrent,
    CFRunLoopRun,
    kCGEventKeyDown,
    kCGEventFlagMaskAlternate,
    kCGEventFlagMaskCommand,
    kCGEventFlagMaskControl,
    kCGEventFlagMaskShift,
    kCGHeadInsertEventTap,
    kCGKeyboardEventAutorepeat,
    kCGKeyboardEventKeycode,
    kCGSessionEventTap,
    kCGEventTapOptionListenOnly,
    kCFRunLoopCommonModes,
)

log = get_logger(__name__)

# macOS virtual keycode for "M".
_KEY_M = 46


def install_pause_hotkey(callback) -> None:
    """Spawns a daemon thread that calls ``callback()`` on ⌃⌥M keydown."""

    def tap_callback(_proxy, _type, event, _refcon):
        # Drop auto-repeat events. macOS fires keyDown repeatedly while
        # ⌃⌥M is held — without this gate, a 500 ms hold would toggle
        # pause dozens of times. ``kCGKeyboardEventAutorepeat`` is the
        # integer field that's non-zero on repeats and zero on the
        # initial press.
        if CGEventGetIntegerValueField(event, kCGKeyboardEventAutorepeat) != 0:
            return event
        keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
        if keycode != _KEY_M:
            return event
        flags = CGEventGetFlags(event)
        ctrl  = bool(flags & kCGEventFlagMaskControl)
        opt   = bool(flags & kCGEventFlagMaskAlternate)
        shift = bool(flags & kCGEventFlagMaskShift)
        cmd   = bool(flags & kCGEventFlagMaskCommand)
        # Strict match: control+option only. Shift/Command must NOT be held.
        if ctrl and opt and not shift and not cmd:
            try:
                callback()
            except Exception as e:
                log.warning(f"⚠️ hotkey callback error: {e}")
        return event

    def run():
        mask = CGEventMaskBit(kCGEventKeyDown)
        tap = CGEventTapCreate(
            kCGSessionEventTap,
            kCGHeadInsertEventTap,
            kCGEventTapOptionListenOnly,
            mask,
            tap_callback,
            None,
        )
        if not tap:
            log.warning("⚠️ Could not install global hotkey "
                  "(Accessibility permission likely missing).")
            return
        src = CFMachPortCreateRunLoopSource(None, tap, 0)
        CFRunLoopAddSource(CFRunLoopGetCurrent(), src, kCFRunLoopCommonModes)
        CGEventTapEnable(tap, True)
        log.info("⌨️  Global hotkey ⌃⌥M registered (pause/resume cursor).")
        CFRunLoopRun()

    threading.Thread(target=run, daemon=True).start()
