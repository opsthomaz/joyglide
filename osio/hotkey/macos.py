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
    kCGEventTapDisabledByTimeout,
    kCGEventTapDisabledByUserInput,
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


def _make_tap_callback(callback, tap_holder: list):
    """Build the CGEventTap callback.

    ``tap_holder`` is a one-element list that ``install_pause_hotkey``
    fills with the tap once ``CGEventTapCreate`` returns — the callback
    is needed *before* the tap exists, and it must be able to re-enable
    that same tap later.
    """

    def tap_callback(_proxy, event_type, event, _refcon):
        # WindowServer disables a tap whose callback is too slow (a Python
        # callback stalled by the GIL during a BLE burst is enough) or on
        # certain user-input transitions. It reports that by calling us
        # with one of these pseudo event types; unless we re-enable, the
        # hotkey is silently dead until the app restarts.
        if event_type in (kCGEventTapDisabledByTimeout, kCGEventTapDisabledByUserInput):
            if tap_holder:
                CGEventTapEnable(tap_holder[0], True)
                log.warning("⌨️  Hotkey event tap was disabled by the system — re-enabled.")
            return event
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

    return tap_callback


def install_pause_hotkey(callback) -> None:
    """Spawns a daemon thread that calls ``callback()`` on ⌃⌥M keydown."""
    tap_holder: list = []
    tap_callback = _make_tap_callback(callback, tap_holder)

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
        tap_holder.append(tap)
        src = CFMachPortCreateRunLoopSource(None, tap, 0)
        CFRunLoopAddSource(CFRunLoopGetCurrent(), src, kCFRunLoopCommonModes)
        CGEventTapEnable(tap, True)
        log.info("⌨️  Global hotkey ⌃⌥M registered (pause/resume cursor).")
        CFRunLoopRun()

    threading.Thread(target=run, daemon=True).start()
