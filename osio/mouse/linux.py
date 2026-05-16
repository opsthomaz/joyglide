# SPDX-License-Identifier: GPL-3.0-or-later
"""Linux backend for cursor injection via the kernel's uinput.

Uinput is the kernel facility for creating virtual input devices. Events
posted to a uinput device flow through the same input subsystem as real
hardware mice — Xorg, Wayland, console, all see them. That makes uinput
the right injection point for a cross-display-server input app:

  * Works under X11 (Xorg), Wayland (any compositor), and on bare TTY.
  * Doesn't require root — kernel grants access to ``/dev/uinput`` via
    a ``udev`` rule (``KERNEL=="uinput", GROUP="input"``) on most
    distros, or per-user via ``setfacl``. If the user can't open
    ``/dev/uinput``, ``UInput()`` raises a clear ``PermissionError``.
  * Sub-pixel motion is software-accumulated (uinput emits integer
    REL_X / REL_Y deltas), matching the Windows backend's behaviour.

We use the ``python-evdev`` library's ``UInput`` wrapper rather than
calling the uinput ioctls directly — the binding is small, well-tested,
and is what every other Linux input project (coffincolors/jc2mouse,
solaar, evtest-cmp) standardises on. It's listed as a Linux-only
extra in ``requirements.txt`` so the macOS/Windows builds don't pull
it in.

Public API matches ``osio.mouse.macos.InputSimulator`` /
``osio.mouse.windows.InputSimulator``:
  ``mouse_move``, ``mouse_down/up``, ``mouse_down_right/up_right``,
  ``mouse_double_click``, ``mouse_scroll`` + the ``refresh_rate`` field.
"""
import time

# evdev is a Linux-only optional dep, listed in requirements.txt with
# `; sys_platform == 'linux'`. This module is only imported by
# ``osio/mouse/__init__.py`` on Linux, so the import is unconditional —
# if evdev isn't installed on a Linux box, the user has a broken install
# and the resulting ImportError is the correct, immediate failure mode
# (delaying it via try/except just makes diagnosis harder).
from collections.abc import Sequence

from evdev import UInput, ecodes as e

from applog import get_logger

log = get_logger(__name__)


# Capability dict registered with the kernel. The tuples list every
# event code we'll emit; the kernel rejects any code outside this set,
# so listing them up front catches typos at device-creation time
# rather than at first-event time.
_CAPABILITIES: dict[int, Sequence[int]] = {
    # BTN_SIDE / BTN_EXTRA are the standard 5-button-mouse "back" /
    # "forward" buttons. Browsers + file managers honour them by
    # default — same convention as Windows X1/X2 and macOS button 3/4.
    e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE,
               e.BTN_SIDE, e.BTN_EXTRA],
    e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL, e.REL_HWHEEL],
}


def _detect_refresh_rate() -> float:
    """Best-effort display refresh detection on Linux.

    We deliberately avoid pulling in Xlib / wayland-client just for
    this — those would conflict with running under TTY or remote
    sessions. Instead:
      * Try ``$XRANDR`` if available (works under X11)
      * Otherwise fall back to 60.0 Hz, the conservative default.

    A wrong refresh rate degrades cursor smoothness slightly but
    doesn't break anything; the pump just runs faster or slower than
    the actual VSync.
    """
    import os
    import subprocess
    try:
        if not os.environ.get("DISPLAY"):
            return 60.0
        out = subprocess.check_output(["xrandr", "--current"],
                                       stderr=subprocess.DEVNULL,
                                       text=True, timeout=1.0)
        # Parse the first line ending in `*` (current mode).
        for line in out.splitlines():
            if "*" in line:
                # Pull the rate immediately before the asterisk.
                # Sample line: "  1920x1080     60.00*+  59.94"
                for token in line.split():
                    if token.endswith("*") or token.endswith("*+"):
                        try:
                            return float(token.rstrip("*+"))
                        except ValueError:
                            pass
    except (FileNotFoundError, subprocess.TimeoutExpired,
             subprocess.CalledProcessError, OSError):
        pass
    return 60.0


def check_accessibility() -> bool:
    """Linux has no per-app accessibility prompt — kernel-level uinput
    permission is granted by the udev rule + group membership at the
    OS configuration layer, not at runtime."""
    return True


class InputSimulator:
    """Linux cursor injection backend via uinput.

    Mirrors the public surface of the macOS / Windows backends. Two
    notable differences from macOS:
      * Sub-pixel motion is software-accumulated (uinput is integer-only,
        same as Win32 ``SendInput``). Fractional dx/dy is held in
        ``_frac_dx/_frac_dy`` until it crosses an integer boundary.
      * Double-click detection is OFF here — the user's compositor
        (X11 or Wayland) handles that at the toolkit/app layer based
        on its own DoubleClickTime config. Adding a second layer would
        compound timings and feel wrong.
    """

    def __init__(self) -> None:
        self._left_down  = False
        self._right_down = False
        self._frac_dx = 0.0
        self._frac_dy = 0.0
        self._last_click_time = 0.0

        try:
            self._ui = UInput(_CAPABILITIES,
                               name="Joyglide virtual mouse",
                               vendor=0x057E, product=0x2009, version=0x0100)
        except (PermissionError, OSError) as ex:
            # Most-common cause: /dev/uinput exists but is mode 0600 and
            # owned by root. Distros expect the user to be in `input`
            # group + the udev rule to set group ownership. Give a
            # clear, actionable error.
            raise RuntimeError(
                f"Cannot open /dev/uinput ({ex}). Add yourself to the "
                f"`input` group (`sudo usermod -a -G input $USER`) and "
                f"log out / log back in, OR install a udev rule that "
                f"grants your user access. See docs/LINUX.md (TODO) "
                f"for details."
            ) from ex

        self.refresh_rate = _detect_refresh_rate()
        log.info(f"🖥️  uinput virtual mouse created @ {self.refresh_rate}Hz")

    def _sync_pos(self) -> None:
        """No-op on Linux — relative motion via uinput tracks cursor implicitly."""

    def mouse_move(self, dx: float, dy: float) -> None:
        """Apply a sub-pixel relative motion. Like the Windows backend,
        fractional dx/dy accumulates until it crosses an integer
        boundary, at which point a single REL_X+REL_Y syn report is
        emitted."""
        self._frac_dx += dx
        self._frac_dy += dy
        idx = int(self._frac_dx)
        idy = int(self._frac_dy)
        if idx == 0 == idy:
            return
        self._frac_dx -= idx
        self._frac_dy -= idy
        if idx:
            self._ui.write(e.EV_REL, e.REL_X, idx)
        if idy:
            self._ui.write(e.EV_REL, e.REL_Y, idy)
        self._ui.syn()

    def mouse_down(self) -> None:
        """Press the left mouse button."""
        self._left_down = True
        self._last_click_time = time.time()
        self._ui.write(e.EV_KEY, e.BTN_LEFT, 1)
        self._ui.syn()

    def mouse_up(self) -> None:
        """Release the left mouse button."""
        self._left_down = False
        self._ui.write(e.EV_KEY, e.BTN_LEFT, 0)
        self._ui.syn()

    def mouse_down_right(self) -> None:
        """Press the right mouse button."""
        self._right_down = True
        self._ui.write(e.EV_KEY, e.BTN_RIGHT, 1)
        self._ui.syn()

    def mouse_up_right(self) -> None:
        """Release the right mouse button."""
        self._right_down = False
        self._ui.write(e.EV_KEY, e.BTN_RIGHT, 0)
        self._ui.syn()

    def mouse_down_middle(self) -> None:
        """Press the middle mouse button."""
        self._ui.write(e.EV_KEY, e.BTN_MIDDLE, 1)
        self._ui.syn()

    def mouse_up_middle(self) -> None:
        """Release the middle mouse button."""
        self._ui.write(e.EV_KEY, e.BTN_MIDDLE, 0)
        self._ui.syn()

    def mouse_down_back(self) -> None:
        """Press BTN_SIDE — browser / file-manager back."""
        self._ui.write(e.EV_KEY, e.BTN_SIDE, 1)
        self._ui.syn()

    def mouse_up_back(self) -> None:
        """Release BTN_SIDE."""
        self._ui.write(e.EV_KEY, e.BTN_SIDE, 0)
        self._ui.syn()

    def mouse_down_forward(self) -> None:
        """Press BTN_EXTRA — browser / file-manager forward."""
        self._ui.write(e.EV_KEY, e.BTN_EXTRA, 1)
        self._ui.syn()

    def mouse_up_forward(self) -> None:
        """Release BTN_EXTRA."""
        self._ui.write(e.EV_KEY, e.BTN_EXTRA, 0)
        self._ui.syn()

    def mouse_double_click(self) -> None:
        """Emit two press/release pairs back-to-back. The compositor
        will interpret them as a double-click based on its own timing
        config — Linux has no equivalent of macOS's
        ``kCGMouseEventClickState``."""
        for value in (1, 0, 1, 0):
            self._ui.write(e.EV_KEY, e.BTN_LEFT, value)
            self._ui.syn()

    def mouse_scroll(self, dx: int, dy: int) -> None:
        """Emit a 2-axis wheel scroll. Linux uinput uses REL_WHEEL /
        REL_HWHEEL with values in "wheel notches" — we approximate
        the macOS-pixel input by scaling 1 notch per 4 pixels (matches
        the Windows backend's SCALE = 4)."""
        SCALE = 4
        if dy:
            self._ui.write(e.EV_REL, e.REL_WHEEL, int(dy / SCALE) or (1 if dy > 0 else -1))
        if dx:
            self._ui.write(e.EV_REL, e.REL_HWHEEL, int(dx / SCALE) or (1 if dx > 0 else -1))
        if dx or dy:
            self._ui.syn()
