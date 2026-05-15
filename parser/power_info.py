# SPDX-License-Identifier: GPL-3.0-or-later
"""Power Info parser — firmware-computed battery state from input
reports 0x07 / 0x08 / 0x09 / 0x0A (the side-specific channels).

The Joy-Con 2 firmware runs its own state-of-charge estimation
internally and publishes the result as a 4-bit level (0..9) in the
"Power Info" bitfield at offset 0x1 of every side-specific input
report. This is the SAME number the Switch console reads to draw its
battery icon — so reading it here gives us authoritative parity with
Nintendo's UI rather than our own voltage-derived approximation.

Layout (from research/ndeadly_switch2/hid_reports.md, identical across
input reports 0x07/0x08/0x09/0x0A):

    Offset 0x1, 1 byte, "Power Info" bitfield:
      bit 0      external power present
      bit 1      charging
      bits 2..5  battery level (0..9)        ← firmware SoC estimation
      bits 6..7  reserved

Why we keep voltage parsing too: this report is on a different GATT
characteristic than input report 0x05 (where mouse / IMU / buttons
live). If the user is on a controller variant we can't subscribe to
(or the second subscribe fails), parser/battery falls back to the
voltage-based percentage. Both pathways update the same
``state.battery_pct`` field, so callers don't need to know which
source is currently authoritative.

The 0..9 → 0..100% mapping is linear via ``round(level * 100 / 9)``,
giving 0=0%, 5=56%, 9=100%. Bucket midpoint conventions (0=5%, 9=95%)
were considered but rejected — using the endpoints lets the dashboard
show "100%" when the firmware says "fully charged" and "0%" when it
says "empty", matching Switch console UI intuition.
"""
import time

from applog import get_logger

log = get_logger(__name__)


# Throttle the firmware-level update to once per second. The
# side-specific report streams at the BLE rate (~33 Hz on macOS,
# ~67 Hz on Windows) but the level only changes once every several
# minutes at most — recomputing every packet would be pure waste.
_THROTTLE_SECONDS = 1.0


def parse(state, data: bytes) -> None:
    """Update ``state.battery_*`` from the side-specific report's Power Info.

    No-op if ``data`` is too short to contain byte 0x1 (degenerate
    packet at reconnect time). Throttled to 1 Hz internally so the
    dashboard doesn't churn on every packet.

    On success, sets:
      * ``state.battery_external_power`` — bool
      * ``state.battery_charging``       — bool (overrides the
        voltage-charge-byte derivation in parser.battery)
      * ``state.battery_full``           — bool (level == 9 + charging)
      * ``state.battery_level_raw``      — int 0..9 firmware level
      * ``state.battery_pct``            — int 0..100, derived
      * ``state.battery_pct_source``     — "firmware" (vs "voltage"
        when only the voltage approximation is available)
    """
    if len(data) < 2:
        return

    now = time.monotonic()
    if now - getattr(state, "_power_info_last_ts", 0.0) < _THROTTLE_SECONDS:
        return

    pi = data[1]
    external = bool(pi & 0x01)
    charging = bool(pi & 0x02)
    level = (pi >> 2) & 0x0F  # 4-bit field; valid values 0..9 per docs

    # Firmware sometimes returns level=0xF as a "level unknown / not
    # ready" sentinel during boot. Reject any level outside 0..9 and
    # leave state untouched until the next valid frame.
    if level > 9:
        return

    state._power_info_last_ts = now
    state.battery_external_power = external
    state.battery_charging       = charging
    state.battery_level_raw      = level
    state.battery_full           = (level == 9) and (charging or external)
    # Linear map 0..9 → 0..100 with both endpoints exact (0→0, 9→100).
    state.battery_pct            = round(level * 100 / 9)
    state.battery_pct_source     = "firmware"


def log_active_subscription(side: str) -> None:
    """Helper used by the BLE wiring code to emit a single info-level
    log line confirming we're now reading the firmware battery level.
    Kept here so all power-info-related strings live next to the
    parser that owns the field."""
    log.info(f"📊 Subscribed to side-specific report ({side}) — "
             f"firmware battery level active.")
