# SPDX-License-Identifier: GPL-3.0-or-later
"""Windows backend for cursor injection — optimized hot path.

Uses Win32 SendInput via ctypes — same API real mice use, no extra deps.
Mirrors the public surface of mouse_macos.InputSimulator so the rest of
the app is platform-agnostic.

Optimization choices for the BLE→cursor hot path on Windows:

  1. Single cached _INPUT struct, reused across calls. We mutate fields
     in-place and re-fire SendInput; no per-call ctypes allocation.

  2. Explicit argtypes/restype on user32 hot functions, so ctypes uses its
     fast-path marshalling instead of inferring per call.

  3. Float position accumulator — Win32 SendInput is integer-only, but we
     keep the fractional part across calls so sub-pixel motion isn't lost
     (matches the macOS Quartz behaviour visually).

  4. Cursor position is read on demand only when a click happens; the hot
     mouse_move path does not call GetCursorPos.
"""
import ctypes
import time
from ctypes import wintypes
from applog import get_logger

log = get_logger(__name__)


# ── ctypes plumbing for SendInput ────────────────────────────────────────
user32 = ctypes.WinDLL("user32", use_last_error=True)

ULONG_PTR = ctypes.c_size_t

class _MOUSEINPUT(ctypes.Structure):
    """ctypes mirror of Win32 ``MOUSEINPUT`` (winuser.h). Holds dx/dy
    relative or absolute coords, button-event flags, and a mouseData
    field used for wheel ticks."""
    _fields_ = [
        ("dx",          wintypes.LONG),
        ("dy",          wintypes.LONG),
        ("mouseData",   wintypes.DWORD),
        ("dwFlags",     wintypes.DWORD),
        ("time",        wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]

class _INPUT(ctypes.Structure):
    """ctypes mirror of Win32 ``INPUT`` — a tagged union over MOUSEINPUT,
    KEYBDINPUT, HARDWAREINPUT. We only use the mouse variant so the
    inner ``_U`` union exposes just ``mi``."""

    class _U(ctypes.Union):
        """Inner anonymous union — Win32 packs all input-source structs
        in here. We only declare ``mi`` because we never inject keyboard
        or hardware events."""
        _fields_ = [("mi", _MOUSEINPUT)]

    _anonymous_ = ("_u",)
    _fields_ = [("type", wintypes.DWORD), ("_u", _U)]

# Bind argtypes/restype once at import — eliminates per-call inference.
user32.SendInput.argtypes        = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
user32.SendInput.restype         = wintypes.UINT
user32.GetCursorPos.argtypes     = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype      = wintypes.BOOL
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype  = ctypes.c_int


INPUT_MOUSE = 0
MOUSEEVENTF_MOVE          = 0x0001
MOUSEEVENTF_LEFTDOWN      = 0x0002
MOUSEEVENTF_LEFTUP        = 0x0004
MOUSEEVENTF_RIGHTDOWN     = 0x0008
MOUSEEVENTF_RIGHTUP       = 0x0010
MOUSEEVENTF_MIDDLEDOWN    = 0x0020
MOUSEEVENTF_MIDDLEUP      = 0x0040
MOUSEEVENTF_XDOWN         = 0x0080
MOUSEEVENTF_XUP           = 0x0100
# X-buttons (5-button mouse extra). XBUTTON1 = back, XBUTTON2 = forward
# per Microsoft convention; same routing every browser respects.
XBUTTON1 = 0x0001
XBUTTON2 = 0x0002
MOUSEEVENTF_WHEEL         = 0x0800
MOUSEEVENTF_HWHEEL        = 0x01000
MOUSEEVENTF_MOVE_NOCOALESCE = 0x2000  # Vista+: prevents the OS from coalescing
                                       # adjacent move events. Critical when the
                                       # pump emits faster than display refresh —
                                       # without this, two consecutive moves can
                                       # be merged into one and we'd lose motion.

# Single MOVE flag we use everywhere — bake the NOCOALESCE in.
_MOVE_FLAGS = MOUSEEVENTF_MOVE | MOUSEEVENTF_MOVE_NOCOALESCE

WHEEL_DELTA = 120  # one notch per Microsoft convention

# Single cached input struct, reused across calls. Avoids ~100 bytes of
# ctypes object construction per cursor event.
_input_buf      = _INPUT()
_input_buf.type = INPUT_MOUSE
_INPUT_SIZE     = ctypes.sizeof(_INPUT)
_INPUT_PTR      = ctypes.byref(_input_buf)


def _send_mouse(flags, dx=0, dy=0, mouseData=0):
    """Fill the cached MOUSEINPUT struct in place and post a single
    SendInput. The cached struct + cached pointer avoid ~100 bytes of
    ctypes object construction per cursor event — meaningful at 60-120Hz
    pump rates where this gets called constantly."""
    mi = _input_buf.mi
    mi.dx          = dx
    mi.dy          = dy
    mi.mouseData   = mouseData
    mi.dwFlags     = flags
    mi.time        = 0
    mi.dwExtraInfo = 0
    user32.SendInput(1, _INPUT_PTR, _INPUT_SIZE)


class _DEVMODEW(ctypes.Structure):
    """ctypes mirror of Win32 ``DEVMODEW`` — used to query the display's
    refresh rate via EnumDisplaySettingsW. We only read
    ``dmDisplayFrequency`` but the full struct is required by the API."""
    _fields_ = [
        ("dmDeviceName", wintypes.WCHAR * 32),
        ("dmSpecVersion", wintypes.WORD),
        ("dmDriverVersion", wintypes.WORD),
        ("dmSize", wintypes.WORD),
        ("dmDriverExtra", wintypes.WORD),
        ("dmFields", wintypes.DWORD),
        ("dmPosition_x", wintypes.LONG),
        ("dmPosition_y", wintypes.LONG),
        ("dmDisplayOrientation", wintypes.DWORD),
        ("dmDisplayFixedOutput", wintypes.DWORD),
        ("dmColor", wintypes.SHORT),
        ("dmDuplex", wintypes.SHORT),
        ("dmYResolution", wintypes.SHORT),
        ("dmTTOption", wintypes.SHORT),
        ("dmCollate", wintypes.SHORT),
        ("dmFormName", wintypes.WCHAR * 32),
        ("dmLogPixels", wintypes.WORD),
        ("dmBitsPerPel", wintypes.DWORD),
        ("dmPelsWidth", wintypes.DWORD),
        ("dmPelsHeight", wintypes.DWORD),
        ("dmDisplayFlags", wintypes.DWORD),
        ("dmDisplayFrequency", wintypes.DWORD),
        ("dmICMMethod", wintypes.DWORD),
        ("dmICMIntent", wintypes.DWORD),
        ("dmMediaType", wintypes.DWORD),
        ("dmDitherType", wintypes.DWORD),
        ("dmReserved1", wintypes.DWORD),
        ("dmReserved2", wintypes.DWORD),
        ("dmPanningWidth", wintypes.DWORD),
        ("dmPanningHeight", wintypes.DWORD),
    ]


class _DISPLAY_DEVICEW(ctypes.Structure):
    """ctypes mirror of Win32 ``DISPLAY_DEVICEW`` — used by
    EnumDisplayDevicesW to enumerate monitors. We need this to walk
    multi-monitor setups and pick the highest refresh rate."""
    _fields_ = [
        ("cb",           wintypes.DWORD),
        ("DeviceName",   wintypes.WCHAR * 32),
        ("DeviceString", wintypes.WCHAR * 128),
        ("StateFlags",   wintypes.DWORD),
        ("DeviceID",     wintypes.WCHAR * 128),
        ("DeviceKey",    wintypes.WCHAR * 128),
    ]


# DISPLAY_DEVICE state flag — only adapters/monitors with this bit set are
# attached to the desktop and contribute to multi-monitor cursor space.
DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x00000001
ENUM_CURRENT_SETTINGS = -1


def _refresh_rate() -> float:
    """Return the **maximum** refresh rate across all attached displays.

    Multi-monitor setups can mix refresh rates; we want the pump fast
    enough for the fastest panel in case the user moves windows around.
    Picks via ``EnumDisplayDevicesW`` + ``EnumDisplaySettingsW`` so
    secondary monitors are visible.

    Falls back to 60 Hz if anything fails. Only read once at startup.
    """
    rates: list[float] = []
    try:
        i = 0
        while True:
            dev = _DISPLAY_DEVICEW()
            dev.cb = ctypes.sizeof(_DISPLAY_DEVICEW)
            if not user32.EnumDisplayDevicesW(None, i, ctypes.byref(dev), 0):
                break
            i += 1
            if not (dev.StateFlags & DISPLAY_DEVICE_ATTACHED_TO_DESKTOP):
                continue
            dm = _DEVMODEW()
            dm.dmSize = ctypes.sizeof(_DEVMODEW)
            if user32.EnumDisplaySettingsW(dev.DeviceName, ENUM_CURRENT_SETTINGS, ctypes.byref(dm)):
                hz = float(dm.dmDisplayFrequency)
                if 30.0 <= hz <= 500.0:
                    rates.append(hz)
    except Exception as e:
        # Defensive — display enumeration is fragile (missing/disconnected
        # adapters, driver quirks, ctypes type errors). We always have the
        # 60 Hz fallback below, so don't crash startup over this.
        log.debug(f"display enum failed ({e}); falling back to 60 Hz")
    return max(rates) if rates else 60.0


def check_accessibility() -> bool:
    """Windows has no per-app accessibility prompt for SendInput."""
    return True


class InputSimulator:
    """Windows cursor injection backend via Win32 SendInput (ctypes).

    Same public API as the macOS variant. Two notable differences:
      * Sub-pixel motion is software-accumulated because SendInput only
        accepts integer dx/dy. Fractional motion is held in
        ``_frac_dx/_frac_dy`` until it crosses an integer boundary.
      * Double-click detection is OFF here — Windows already does it
        at the OS layer based on DoubleClickTime, so we just relay
        down/up and let the system decide.
    """

    def __init__(self) -> None:
        """Probe screen dimensions and refresh rate. Initialise the
        sub-pixel accumulator at zero."""
        self._left_down  = False
        self._right_down = False
        self._last_click_time = 0.0
        # Sub-pixel accumulator — Windows SendInput only takes ints.
        self._frac_dx = 0.0
        self._frac_dy = 0.0

        SM_CXSCREEN, SM_CYSCREEN = 0, 1
        sw = user32.GetSystemMetrics(SM_CXSCREEN)
        sh = user32.GetSystemMetrics(SM_CYSCREEN)
        self.refresh_rate = _refresh_rate()
        log.info(f"🖥️  Display Detected: {sw}x{sh} @ {self.refresh_rate}Hz")

    def _sync_pos(self) -> None:
        """No-op on Windows — relative motion via SendInput tracks cursor implicitly."""

    def mouse_move(self, dx: float, dy: float) -> None:
        """Apply a sub-pixel relative motion. Fractional dx/dy
        accumulates until it crosses an integer boundary, at which point
        we emit a SendInput with the integer part and keep the leftover
        fraction for the next call."""
        # Accumulate fractional motion; emit integer pixel deltas only.
        self._frac_dx += dx
        self._frac_dy += dy
        idx = int(self._frac_dx)
        idy = int(self._frac_dy)
        if idx == 0 == idy:
            return
        self._frac_dx -= idx
        self._frac_dy -= idy
        _send_mouse(_MOVE_FLAGS, idx, idy)

    def mouse_down(self) -> None:
        """Press the left mouse button. No double-click counting needed —
        Windows handles that at the OS layer."""
        self._left_down = True
        # Windows handles double-click detection at the OS layer (DoubleClickTime),
        # so we just relay the down/up events and let the system decide.
        self._last_click_time = time.time()
        _send_mouse(MOUSEEVENTF_LEFTDOWN)

    def mouse_up(self) -> None:
        """Release the left mouse button."""
        self._left_down = False
        _send_mouse(MOUSEEVENTF_LEFTUP)

    def mouse_down_right(self) -> None:
        """Press the right mouse button."""
        self._right_down = True
        _send_mouse(MOUSEEVENTF_RIGHTDOWN)

    def mouse_up_right(self) -> None:
        """Release the right mouse button."""
        self._right_down = False
        _send_mouse(MOUSEEVENTF_RIGHTUP)

    def mouse_down_middle(self) -> None:
        """Press the middle mouse button."""
        _send_mouse(MOUSEEVENTF_MIDDLEDOWN)

    def mouse_up_middle(self) -> None:
        """Release the middle mouse button."""
        _send_mouse(MOUSEEVENTF_MIDDLEUP)

    def mouse_down_back(self) -> None:
        """Press XBUTTON1 (browser/Finder back)."""
        _send_mouse(MOUSEEVENTF_XDOWN, mouseData=XBUTTON1)

    def mouse_up_back(self) -> None:
        """Release XBUTTON1."""
        _send_mouse(MOUSEEVENTF_XUP, mouseData=XBUTTON1)

    def mouse_down_forward(self) -> None:
        """Press XBUTTON2 (browser/Finder forward)."""
        _send_mouse(MOUSEEVENTF_XDOWN, mouseData=XBUTTON2)

    def mouse_up_forward(self) -> None:
        """Release XBUTTON2."""
        _send_mouse(MOUSEEVENTF_XUP, mouseData=XBUTTON2)

    def mouse_double_click(self) -> None:
        """Emit an explicit down/up/down/up sequence for callers that
        want to force a double-click independent of OS timing."""
        for f in (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP,
                  MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP):
            _send_mouse(f)

    def mouse_scroll(self, dx: int, dy: int) -> None:
        """Emit a 2-axis wheel scroll. Inputs are in our pixel-ish unit
        (matching the macOS backend); we convert to Windows' "1 notch =
        120 units" scale via the SCALE divisor."""
        # macOS scroll units are pixel-ish; Windows wheel uses 1 notch = 120.
        # Approximate by scaling our 1px-equivalent to a small notch fraction.
        SCALE = 4  # 4 mouse pixels per scroll notch — empirically reasonable
        if dy:
            _send_mouse(MOUSEEVENTF_WHEEL,  mouseData=(dy * (WHEEL_DELTA // SCALE)) & 0xFFFFFFFF)
        if dx:
            _send_mouse(MOUSEEVENTF_HWHEEL, mouseData=(dx * (WHEEL_DELTA // SCALE)) & 0xFFFFFFFF)
