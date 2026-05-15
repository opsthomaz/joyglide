# SPDX-License-Identifier: GPL-3.0-or-later
"""Magnetometer parser for input report 0x05 — 3-axis raw counts.

The Joy-Con 2 ships a 3-axis magnetometer. When feature bit 7
(`FEATURE_MAGNETOMETER`, 0x80) is enabled in the feature mask, the
controller emits a 6-byte block at offset ``MAG_OFFSET = 0x19`` of
input report 0x05: three s16 LE values representing X, Y, Z field
strength in raw sensor counts.

Layout sourced from ``research/ndeadly_switch2/hid_reports.md`` and
cross-checked against ``TropicalCyclone/switch2-controller-driver``
(``data[25:27]``, ``[27:29]``, ``[29:31]`` LE signed). Both agree.

What this enables (no current consumer — shipped as data plumbing):

  * **Air-mouse mode** (planned in `docs/ROADMAP.md`) — combined with
    the IMU's gyro, the magnetometer gives a north-locked direction
    reference that prevents the gyro-only drift problem. Real
    Wii-Remote-style absolute pointing.
  * **Gesture / orientation features** — "is the controller pointing
    at the screen?" classification.
  * **Diagnostic verification** — set ``settings["magnetometer_dump_raw"]
    = True`` to log raw values per packet.

Calibration: Nintendo doesn't publish per-controller magnetometer
offsets. Absolute heading would need calibration data from the SPI
flash, which is currently behind the unimplemented Nintendo handshake
(see `docs/ARCHITECTURE.md` §"Nintendo Pairing Handshake"). For
relative orientation tracking (delta from a reference pose) the raw
values are usable directly.

Default off — opt-in via ``magnetometer_enabled``. The
FEATURE_MAGNETOMETER bit is already in `FEATURE_MASK_DEFAULT`
(0xFF), so the controller is already emitting the bytes; the setting
just gates whether we *parse* them on every packet.
"""
import struct

from applog import get_logger
from parser.constants import MAG_BLOCK_LEN, MAG_OFFSET
from user_preferences import settings

log = get_logger(__name__)

# Pre-compile the struct format once at import: little-endian × 3 s16.
_MAG_STRUCT = struct.Struct("<3h")


def parse(state, data: bytes) -> None:
    """Decode the magnetometer block and stash the (x, y, z) tuple on ``state``.

    No-ops cleanly when:
      * Magnetometer parsing is disabled (`magnetometer_enabled`
        defaults to False) — saves the parsing cost on every packet
        for users who don't opt in.
      * The packet is too short to contain the magnetometer bytes.

    On success, sets ``state.magnetometer = (mx, my, mz)`` as raw s16
    counts. Otherwise leaves the previous value in place (or None on
    first parse).
    """
    if not settings.get("magnetometer_enabled", False):
        return
    if len(data) < MAG_OFFSET + MAG_BLOCK_LEN:
        return

    mx, my, mz = _MAG_STRUCT.unpack_from(data, MAG_OFFSET)
    state.magnetometer = (mx, my, mz)

    # Diagnostic mode — useful for verifying offset / signedness on
    # hardware: rotating the JC2 around each axis should swing the
    # corresponding magnetometer reading through zero (after the
    # geomagnetic offset is subtracted).
    if settings.get("magnetometer_dump_raw", False):
        raw = data[MAG_OFFSET:MAG_OFFSET + MAG_BLOCK_LEN].hex()
        log.info(f"  🧲 mag raw={raw} ({mx:+6d}, {my:+6d}, {mz:+6d})")
