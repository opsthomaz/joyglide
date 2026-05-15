# SPDX-License-Identifier: GPL-3.0-or-later
"""Windows backend for the global pause hotkey (⌃⌥M / Ctrl+Alt+M).

Uses RegisterHotKey + a per-thread message pump (GetMessage). Same idea
as the macOS CGEventTap version but via Win32 instead of Quartz.
"""
import ctypes
import threading
from ctypes import wintypes
from applog import get_logger

log = get_logger(__name__)


user32 = ctypes.WinDLL("user32", use_last_error=True)

# Modifier flags for RegisterHotKey
MOD_ALT     = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT   = 0x0004
MOD_WIN     = 0x0008
MOD_NOREPEAT = 0x4000  # Vista+, prevents auto-repeat firing the hotkey twice

VK_M = 0x4D
WM_HOTKEY = 0x0312
WM_QUIT   = 0x0012

_HOTKEY_ID = 1


def install_pause_hotkey(callback) -> None:
    """Register Ctrl+Alt+M globally and call ``callback()`` when pressed."""

    def run():
        # RegisterHotKey is per-thread — must be called from the same thread
        # that runs the message pump.
        modifiers = MOD_CONTROL | MOD_ALT | MOD_NOREPEAT
        if not user32.RegisterHotKey(None, _HOTKEY_ID, modifiers, VK_M):
            log.warning(f"⚠️ RegisterHotKey failed (last error: {ctypes.get_last_error()})")
            return
        log.info("⌨️  Global hotkey Ctrl+Alt+M registered (pause/resume cursor).")

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam == _HOTKEY_ID:
                try:
                    callback()
                except Exception as e:
                    log.warning(f"⚠️ hotkey callback error: {e}")
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        user32.UnregisterHotKey(None, _HOTKEY_ID)

    threading.Thread(target=run, daemon=True).start()
