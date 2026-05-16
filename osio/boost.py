# SPDX-License-Identifier: GPL-3.0-or-later
"""Platform-specific boot hooks: priority + power management + BLE tuning.

macOS:
  - NSProcessInfo Anti App-Nap (prevents asyncio timer coalescing in background)
  - os.nice(-10) to nudge scheduler priority
  - No BLE connection-parameter tweak (macOS doesn't honor it for non-HID)

Windows:
  - SetPriorityClass(HIGH_PRIORITY_CLASS) — equivalent of nice
  - PowerSetRequest with PowerRequestSystemRequired — keeps scheduling sharp
    when the lid is closed / Modern Standby would otherwise kick in
  - request_throughput_optimized(client) — calls
    BluetoothLEPreferredConnectionParameters.ThroughputOptimized after
    a bleak connect, dropping the LL interval from 60ms → 15ms (~67Hz)
"""
import sys

from applog import get_logger

log = get_logger(__name__)

# Held forever to keep the Mach activity alive — assigned by ``_boost_macos``.
# Declared at module top (before the function that touches it) so the
# ``global`` reference resolves to the module-level name from first call.
_ANTI_NAP_ACTIVITY = None


# ── Anti-throttle / priority boost ───────────────────────────────────────

def boost_process_priority() -> None:
    """Cross-platform entry — request elevated scheduling priority and
    disable any OS-level throttling that would harm BLE input latency.
    Dispatches to ``_boost_macos`` (NSActivity + os.nice),
    ``_boost_windows`` (HIGH_PRIORITY_CLASS + SetThreadExecutionState +
    timeBeginPeriod), or ``_boost_linux`` (os.nice + best-effort
    inhibitor). No-op on other platforms."""
    if sys.platform == "darwin":
        _boost_macos()
    elif sys.platform == "win32":
        _boost_windows()
    elif sys.platform.startswith("linux"):
        _boost_linux()


def _boost_macos() -> None:
    """macOS-specific priority boost: ``os.nice(-10)`` for scheduler
    priority + NSProcessInfo.beginActivity for Anti App-Nap (which
    otherwise throttles asyncio timers when our window is unfocused)."""
    import os
    try:
        os.nice(-10)
        log.info("⚡ High Priority scheduling active.")
    except PermissionError:
        # Best-effort — os.nice(-10) needs CAP_SYS_NICE / root. Without it
        # we just run at default priority, which works fine.
        log.debug("os.nice(-10) denied — running without priority boost")

    # Anti App-Nap — a separate concern, but fits naturally here.
    try:
        from Foundation import NSProcessInfo
        global _ANTI_NAP_ACTIVITY
        # NSActivityUserInitiatedAllowingIdleSystemSleep = 0x00FFFFFF
        _ANTI_NAP_ACTIVITY = NSProcessInfo.processInfo().beginActivityWithOptions_reason_(
            0x00FFFFFF, "Joyglide low-latency input pump"
        )
        log.info("🛡️ Anti App-Nap protection active.")
    except Exception as e:
        log.warning(f"⚠️ Failed to init Anti App-Nap: {e}")


def _boost_linux() -> None:
    """Linux-specific priority boost.

    Three things we'd like to do, in decreasing order of "actually
    works without root":

      1. ``os.nice(-10)`` — bumps scheduling priority. Needs CAP_SYS_NICE
         or root; harmless to attempt without and silently no-ops via
         the PermissionError path.
      2. systemd-inhibit / GNOME inhibitor — prevents idle suspend, so
         the BT stack doesn't get torn down under us. Skipped here
         because it requires DBus and most distros don't suspend during
         active sessions anyway. If a user reports symptoms, we can
         add a dbus-next conditional path later.
      3. BlueZ HCI LE_Connection_Update — drops the connection
         interval to 5ms (200 Hz), the actual unique-to-Linux win.
         Implemented as a separate ``request_throughput_optimized``
         call invoked from ble.connection.connect_and_setup, since it
         needs the connected client (handle) — see that function for
         the implementation details.
    """
    import os
    try:
        os.nice(-10)
        log.info("⚡ High Priority scheduling active.")
    except PermissionError:
        log.debug("os.nice(-10) denied — running without priority boost")


def _boost_windows() -> None:
    """Windows-specific priority boost. Three things happen here:

      1. ``SetPriorityClass(HIGH_PRIORITY_CLASS)`` bumps our process
         above default. Doesn't need admin.
      2. ``SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED
         | ES_AWAYMODE_REQUIRED)`` keeps the system awake and stops
         Modern Standby from coalescing our timers.
      3. ``timeBeginPeriod(1)`` raises the system timer resolution to
         1ms (default 15.6ms) so ``asyncio.sleep`` actually wakes
         on the 16.67ms boundary required by 60Hz pump.
    """
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    winmm    = ctypes.WinDLL("winmm",    use_last_error=True)

    # Explicit signatures — required on 64-bit Python. Without restype set,
    # ctypes assumes c_int (32-bit) for return values, which truncates the
    # pseudo-handle (HANDLE)-1 returned by GetCurrentProcess() and causes
    # SetPriorityClass to fail with ERROR_INVALID_HANDLE (6). Likewise,
    # ES_CONTINUOUS = 0x80000000 doesn't fit cleanly in c_int.
    kernel32.GetCurrentProcess.restype  = ctypes.c_void_p
    kernel32.SetPriorityClass.argtypes  = [ctypes.c_void_p, ctypes.c_uint]
    kernel32.SetPriorityClass.restype   = ctypes.c_int
    kernel32.SetThreadExecutionState.argtypes = [ctypes.c_uint]
    kernel32.SetThreadExecutionState.restype  = ctypes.c_uint
    winmm.timeBeginPeriod.argtypes = [ctypes.c_uint]
    winmm.timeBeginPeriod.restype  = ctypes.c_uint

    HIGH_PRIORITY_CLASS = 0x00000080
    h_proc = kernel32.GetCurrentProcess()
    if kernel32.SetPriorityClass(h_proc, HIGH_PRIORITY_CLASS):
        log.info("⚡ HIGH_PRIORITY_CLASS active.")
    else:
        log.warning(f"⚠️ SetPriorityClass failed: {ctypes.get_last_error()}")

    # Keep system awake / prevent Modern Standby from coalescing our timers.
    try:
        ES_CONTINUOUS        = 0x80000000
        ES_SYSTEM_REQUIRED   = 0x00000001
        ES_AWAYMODE_REQUIRED = 0x00000040
        prev = kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED
        )
        if prev == 0:
            log.warning(f"⚠️ SetThreadExecutionState returned 0 (last error: {ctypes.get_last_error()})")
        else:
            log.info("🛡️ SetThreadExecutionState set (no system idle sleep).")
    except Exception as e:
        log.warning(f"⚠️ SetThreadExecutionState failed: {e}")

    # Raise the system timer resolution to 1ms (default is 15.6ms). Critical
    # for asyncio.sleep precision in our pump — a 60Hz pump needs 16.67ms
    # period; with 15.6ms timer granularity we'd slip ticks routinely.
    # Trade-off: increases idle power consumption slightly. Acceptable cost
    # for an input-realtime app on a desktop; on laptop the extra wake-ups
    # are still negligible compared to the work the pump itself does.
    try:
        rc = winmm.timeBeginPeriod(1)
        if rc == 0:  # TIMERR_NOERROR
            log.info("⏱️  timeBeginPeriod(1) — system timer resolution at 1ms.")
        else:
            log.warning(f"⚠️ timeBeginPeriod(1) returned {rc} (TIMERR_NOCANDO=97)")
    except Exception as e:
        log.warning(f"⚠️ timeBeginPeriod failed: {e}")


# ── BLE connection parameter tweak (Windows-only effective) ──────────────

async def request_throughput_optimized(client) -> bool:
    """Ask the BLE host for the shortest connection interval it will allow.

    Windows: drops the LL connection interval from 60ms (default for non-HID
    BLE) to 15ms via the WinRT `BluetoothLEPreferredConnectionParameters
    .ThroughputOptimized` preset. That gives ~66.7Hz packet rate — about 2×
    macOS (33Hz) but well below the Switch console's native 5ms/200Hz.

    The 15ms is a fixed value baked into the WinRT preset (verified by
    inspecting `params.min_connection_interval == params.max_connection_interval
    == 12`, where each unit = 1.25ms). The preset is the only public way to
    request shorter intervals; there's no public constructor that accepts
    custom values. So 15ms is the practical Windows ceiling for this API,
    not 7.5ms as some other docs claim.

    macOS: no-op — verified experimentally that no API achieves this on Mac.

    Validated by two production C++ projects that use the same preset
    (also stuck at 15ms despite their optimistic comments):
      - TheFrano/joycon2cpp (testapp.cpp lines 955+, 1209+, 1228+, 1340+, 1406+)
      - Misaka10571/joycon2-connector (DeviceManager.h:185-190)
    """
    if sys.platform.startswith("linux"):
        return await _request_throughput_optimized_linux(client)
    if sys.platform != "win32":
        return False
    # IMPORTANT: import from `winrt`, not `winsdk`. Both are Python projections
    # of the same WinRT API, but their classes are DIFFERENT Python types and
    # their marshallers don't talk to each other. Bleak's BleakClientWinRT
    # backend uses `winrt`, so the BluetoothLEDevice we reach via bleak is a
    # `winrt._winrt_windows_devices_bluetooth.BluetoothLEDevice`. Passing a
    # `winsdk` object to its `request_preferred_connection_parameters` method
    # raises `TypeError: convert_to returned null` because the winrt marshaller
    # can't convert across projections.
    try:
        from winrt.windows.devices.bluetooth import (
            BluetoothLEPreferredConnectionParameters,
        )
    except ImportError:
        log.warning("⚠️ winrt-Windows.Devices.Bluetooth not available — bleak should "
              "have brought it as a transitive dep. Reinstall with "
              "`pip install --upgrade bleak`.")
        return False

    # Reach into bleak's Windows backend for the underlying BluetoothLEDevice.
    backend = getattr(client, "_backend", None)
    requester = None
    if backend is not None:
        for attr in ("_requester", "requester", "_session"):
            obj = getattr(backend, attr, None)
            if obj is not None and hasattr(obj, "request_preferred_connection_parameters"):
                requester = obj
                break
        if requester is None:
            for k in dir(backend):
                v = getattr(backend, k, None)
                if v is not None and hasattr(v, "request_preferred_connection_parameters"):
                    requester = v
                    break
    if requester is None:
        log.warning("⚠️ Could not access WinRT BluetoothLEDevice on bleak backend; "
              "BLE will run at default ~16Hz.")
        return False

    try:
        params = BluetoothLEPreferredConnectionParameters.throughput_optimized
        request = requester.request_preferred_connection_parameters(params)
        # Each unit is 1.25ms per the BLE spec; ThroughputOptimized is min=max=12,
        # i.e. 15ms (~66.7Hz). That's the Windows ceiling via this API — there
        # is no public constructor for arbitrary connection-parameter values.
        ms = params.max_connection_interval * 1.25
        hz = 1000.0 / ms if ms else 0
        log.info(f"⚡ Requested ThroughputOptimized "
              f"(interval={ms:.2f}ms / {hz:.1f}Hz target; status={request.status}).")
        return True
    except Exception as e:
        log.warning(f"⚠️ request_preferred_connection_parameters failed: {e}")
        return False


# ── Linux: BlueZ HCI LE_Connection_Update via best-effort hcitool ────────


async def _request_throughput_optimized_linux(client) -> bool:
    """Linux-specific BLE rate boost via BlueZ HCI LE_Connection_Update.

    Linux is the only OS where the JC2 can actually run at its native
    5ms / 200Hz rate (per docs/RESEARCH.md §1.2), but reaching there
    requires sending the HCI ``LE_Connection_Update`` command (OGF=0x08,
    OCF=0x0013) on the controller's HCI handle. BlueZ exposes this
    via the ``hcitool lecup`` CLI (deprecated but still widely
    shipped), and we use that as a best-effort path here.

    The flow:
      1. Read the BD_ADDR of the connected peer from the bleak client.
      2. Resolve the HCI connection handle via ``hcitool con``.
      3. Issue ``hcitool lecup --handle <h> --min 4 --max 4`` (where
         each unit = 1.25ms → 4 = 5ms ≈ 200Hz).

    Requires either:
      * Root (``sudo``), or
      * ``setcap cap_net_admin+eip /usr/bin/hcitool``, or
      * Membership in a privileged group your distro maps to BT control.

    Falls back gracefully when ``hcitool`` is missing, the user lacks
    permission, or the handle resolution fails. The connection still
    works at the BlueZ-default rate (~30ms) — same as macOS — so this
    is a "nice-to-have" optimization, never a "must-succeed".
    """
    import shutil
    if shutil.which("hcitool") is None:
        log.info("ℹ️  Linux BLE rate boost skipped — `hcitool` not found "
                 "(install bluez-utils for the 200 Hz path).")
        return False

    addr = getattr(client, "address", None)
    if not addr:
        log.debug("Linux BLE rate boost skipped — no client.address")
        return False

    import asyncio
    try:
        # `hcitool con` lists active connections with handles. Output
        # format: "    LE BD_ADDR ... handle 64 state 1 ..."
        proc = await asyncio.create_subprocess_exec(
            "hcitool", "con",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=2.0)
        text = out.decode("utf-8", errors="replace")
    except (FileNotFoundError, TimeoutError, OSError) as ex:
        log.debug(f"hcitool con failed: {ex}")
        return False

    handle: int | None = None
    for line in text.splitlines():
        if addr.lower() in line.lower() and "handle" in line:
            tokens = line.split()
            try:
                hi = tokens.index("handle")
                handle = int(tokens[hi + 1])
                break
            except (ValueError, IndexError):
                continue
    if handle is None:
        log.debug(f"Could not resolve HCI handle for {addr} from hcitool con")
        return False

    # 4 × 1.25ms = 5ms (~200Hz). latency=0, supervision_timeout=300 (3s).
    try:
        proc = await asyncio.create_subprocess_exec(
            "hcitool", "lecup",
            "--handle", str(handle),
            "--min", "4", "--max", "4",
            "--latency", "0", "--timeout", "300",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE)
        _, err = await asyncio.wait_for(proc.communicate(), timeout=2.0)
        if proc.returncode == 0:
            log.info("⚡ Requested BlueZ LE_Connection_Update — 5ms / 200 Hz target.")
            return True
        log.info(f"ℹ️  hcitool lecup returned {proc.returncode}: "
                 f"{err.decode('utf-8', errors='replace').strip()} "
                 f"(needs CAP_NET_ADMIN — run as root or "
                 f"`sudo setcap cap_net_admin+eip $(which hcitool)`).")
    except (FileNotFoundError, TimeoutError, OSError) as ex:
        log.debug(f"hcitool lecup failed: {ex}")
    return False
