# SPDX-License-Identifier: GPL-3.0-or-later
"""IMU parser for input report 0x05 — gyro + accel + temperature + timestamp.

The Joy-Con 2 ships a six-axis IMU. When feature bit 2 (IMU) is enabled
in the feature mask sent via command 0x0C, the controller appends an
18-byte Motion Data block to each input report at offset
``IMU_OFFSET`` (see ``parser.constants``).

Layout AND calibration scales verified against:

  * ``research/ndeadly_switch2/hid_reports.md`` → "Input Report 0x05 →
    Motion Data"
  * Upstream issue github.com/german77/JoyconDriver#1 (March 2026), in
    which german77 and ndeadly explicitly confirmed the offsets AND
    the firmware-level conversion constants for handle 0x000A:

        timestamp: 50_000 ticks = 1 second
        temperature: degC = 25 + raw / 127
        accel: 4096 raw = 1 G
        gyro: 48000 raw = 360 degrees

    The constants are firmware-level (not per-controller flash
    calibration), so they apply to every JC2 unit identically — no
    SPI-flash dance needed to land a calibrated reading.

Honest scope note: input report 0x05 carries a SINGLE IMU sample per
packet, so IMU samples arrive at the same rate as the optical sensor
(33 Hz on macOS, 67 Hz on Windows). Multi-sample motion data exists on
input reports 0x07/0x08 (variable size, "not fully understood" per
ndeadly) — we deliberately don't subscribe to those.

What this enables:
  * **Air-mouse mode** (planned in docs/ROADMAP.md) — gyro Z drives
    cursor X for Wii-Remote-style pointing.
  * **Gesture / orientation features** — accel direction → "is the
    controller flat / tilted / inverted?".
  * **Diagnostic verification** — set ``settings["imu_dump_raw"] =
    True`` to log raw + calibrated values per packet.

Default OFF — opt-in via ``settings["imu_enabled"]``. When off, the
FEATURE_IMU bit is dropped from the mask sent in ``ble.protocol
.enable_mouse``, so the controller doesn't even compute / transmit
the IMU bytes — saves controller-side battery on the optical-only path.
"""
import struct

from applog import get_logger
from parser.constants import (
    IMU_ACCEL_COUNTS_PER_G,
    IMU_BLOCK_LEN,
    IMU_GYRO_COUNTS_PER_360,
    IMU_OFFSET,
    IMU_TEMP_DIVISOR,
    IMU_TEMP_OFFSET_DEG_C,
)
from user_preferences import settings

log = get_logger(__name__)

# Pre-compile the struct format once at import:
#   <      = little-endian
#   I      = u32 timestamp
#   h      = s16 temperature
#   3h     = s16 × 3 accel xyz
#   3h     = s16 × 3 gyro xyz
# struct.unpack_from is faster than slice + int.from_bytes loops on the hot path.
_MOTION_STRUCT = struct.Struct("<Ih3h3h")


def parse(state, data: bytes) -> None:
    """Decode the Motion Data block and stash sub-fields on ``state``.

    No-ops cleanly when:
      * IMU is disabled (``settings["imu_enabled"]`` is False) — saves
        the parsing cost on every packet for users who don't opt in.
      * The packet is too short to contain the IMU bytes — the
        controller may not have actually emitted them yet (feature
        negotiation race on reconnect).

    On success, sets:
      * ``state.imu_timestamp``       — u32 firmware counter (50 kHz)
      * ``state.imu_temperature``     — s16 raw
      * ``state.imu_temperature_c``   — float, degrees Celsius
      * ``state.imu_accel``           — ``(ax, ay, az)`` raw s16
      * ``state.imu_accel_g``         — ``(ax_g, ay_g, az_g)`` floats in G
      * ``state.imu_gyro``            — ``(gx, gy, gz)`` raw s16
      * ``state.imu_gyro_deg``        — ``(gx, gy, gz)`` floats in degrees
        (per sample period — divide by sample dt for deg/s)
    Otherwise these stay at the previous value (or None on first parse).
    """
    if not settings.get("imu_enabled", False):
        return
    if len(data) < IMU_OFFSET + IMU_BLOCK_LEN:
        return

    ts, temp, ax, ay, az, gx, gy, gz = _MOTION_STRUCT.unpack_from(data, IMU_OFFSET)

    # Raw values — what the controller actually sent. Diagnostics and
    # any future per-controller calibration consume these.
    state.imu_timestamp   = ts
    state.imu_temperature = temp
    state.imu_accel       = (ax, ay, az)
    state.imu_gyro        = (gx, gy, gz)

    # Calibrated values — useful directly for air-mouse / gesture code.
    # Constants confirmed in github.com/german77/JoyconDriver#1; no
    # per-controller flash data needed.
    state.imu_temperature_c = IMU_TEMP_OFFSET_DEG_C + temp / IMU_TEMP_DIVISOR
    state.imu_accel_g       = (ax / IMU_ACCEL_COUNTS_PER_G,
                                ay / IMU_ACCEL_COUNTS_PER_G,
                                az / IMU_ACCEL_COUNTS_PER_G)
    state.imu_gyro_deg      = (gx * 360.0 / IMU_GYRO_COUNTS_PER_360,
                                gy * 360.0 / IMU_GYRO_COUNTS_PER_360,
                                gz * 360.0 / IMU_GYRO_COUNTS_PER_360)

    if settings.get("imu_dump_raw", False):
        raw = data[IMU_OFFSET:IMU_OFFSET + IMU_BLOCK_LEN].hex()
        log.info(
            f"  🧭 IMU ts={ts} {state.imu_temperature_c:+.1f}°C "
            f"accel(g)=({state.imu_accel_g[0]:+.3f},"
            f"{state.imu_accel_g[1]:+.3f},"
            f"{state.imu_accel_g[2]:+.3f}) "
            f"gyro(°)=({state.imu_gyro_deg[0]:+.2f},"
            f"{state.imu_gyro_deg[1]:+.2f},"
            f"{state.imu_gyro_deg[2]:+.2f}) "
            f"raw={raw}"
        )
