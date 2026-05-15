# SPDX-License-Identifier: GPL-3.0-or-later
"""Global pause hotkey on Linux via evdev keyboard monitoring.

We listen for ``Ctrl+Alt+M`` directly on every keyboard input device in
``/dev/input/event*`` rather than going through X11 / Wayland — same
reasoning as the mouse backend: kernel-level evdev works across all
display servers, including TTY, and doesn't conflict with Wayland's
restricted global-shortcut model (where capturing system-wide hotkeys
from an unprivileged app is genuinely hard otherwise).

Tradeoff: needs read access to the keyboard's evdev node. Same group
permission as ``/dev/uinput`` (``input`` group + udev rule). On most
distros, adding the user to ``input`` is the one-time setup that
unblocks both this and ``osio.mouse.linux``.

If no keyboard nodes are readable, ``install_pause_hotkey`` falls back
to a clear warning and runs without a hotkey — same behaviour as a
misconfigured macOS Accessibility permission.
"""
import threading

# evdev is a Linux-only dep (requirements.txt with `; sys_platform ==
# 'linux'`). This module is only loaded by osio.hotkey's dispatcher on
# Linux, so the import is unconditional — see osio/mouse/linux.py for
# the same reasoning.
from evdev import InputDevice, ecodes as e, list_devices

from applog import get_logger

log = get_logger(__name__)


def _is_keyboard(dev) -> bool:
    """Return True if the evdev device looks like a keyboard.

    We check that it advertises the EV_KEY capability AND has the
    standard letter keys (KEY_A, KEY_M) — that excludes mice (which
    also report EV_KEY for buttons) and gamepads (which report random
    KEY_BTN_* but not letters).
    """
    try:
        caps = dev.capabilities().get(e.EV_KEY, [])
        return e.KEY_A in caps and e.KEY_M in caps
    except (PermissionError, OSError):
        return False


def install_pause_hotkey(callback) -> None:
    """Spawn a daemon thread per readable keyboard that calls
    ``callback()`` on Ctrl+Alt+M keydown.

    One thread per device because evdev's ``read_loop`` is blocking,
    and Linux machines may legitimately have several keyboards
    (built-in + USB + Bluetooth). The daemon flag means they all
    terminate cleanly when the main app exits.
    """
    devices = []
    for path in list_devices():
        try:
            d = InputDevice(path)
        except (PermissionError, OSError) as ex:
            log.debug(f"can't open {path}: {ex}")
            continue
        if _is_keyboard(d):
            devices.append(d)

    if not devices:
        log.warning("⚠️ No readable keyboard found — global hotkey disabled. "
                     "Add yourself to the `input` group and re-login.")
        return

    def watch(dev):
        """Per-device watcher: track Ctrl/Alt/Shift/Super state and fire
        the callback exactly when M is pressed with Ctrl+Alt held and
        Shift/Super absent (mirrors the macOS strict match)."""
        ctrl = alt = shift = meta = False
        try:
            for ev in dev.read_loop():
                if ev.type != e.EV_KEY:
                    continue
                # Track modifier state on press (1) / release (0); ignore autorepeat (2).
                if ev.value not in (0, 1):
                    continue
                pressed = ev.value == 1
                if ev.code in (e.KEY_LEFTCTRL, e.KEY_RIGHTCTRL):
                    ctrl = pressed
                elif ev.code in (e.KEY_LEFTALT, e.KEY_RIGHTALT):
                    alt = pressed
                elif ev.code in (e.KEY_LEFTSHIFT, e.KEY_RIGHTSHIFT):
                    shift = pressed
                elif ev.code in (e.KEY_LEFTMETA, e.KEY_RIGHTMETA):
                    meta = pressed
                elif ev.code == e.KEY_M and pressed and ctrl and alt and not shift and not meta:
                    try:
                        callback()
                    except Exception as ex:
                        log.warning(f"⚠️ hotkey callback error: {ex}")
        except OSError as ex:
            log.warning(f"⚠️ keyboard {dev.path} disconnected ({ex})")

    for d in devices:
        t = threading.Thread(target=watch, args=(d,), daemon=True,
                              name=f"jc2m-hotkey-{d.path}")
        t.start()
    log.info(f"⌨️  Global hotkey Ctrl+Alt+M registered on {len(devices)} keyboard(s).")
