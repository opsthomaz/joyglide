# SPDX-License-Identifier: GPL-3.0-or-later
"""Input report 0x05 byte offsets — sourced from
ndeadly/switch2_controller_research/hid_reports.md and validated against
german77/JoyconDriver/switch2/input_handler.lua. Both agree.
"""

# ── Optical sensor (mouse) — bytes 0x10..0x17 of report 0x05 ────────────
OPT_X_OFFSET       = 0x10  # u16 LE — absolute X position
OPT_Y_OFFSET       = 0x12  # u16 LE — absolute Y position
OPT_SURFACE_OFFSET = 0x14  # u16 LE — "surface quality?" per ndeadly
OPT_LIFTOFF_OFFSET = 0x16  # u16 LE — "lift-off distance?" per ndeadly

# Optical-sensor deadzone in raw sensor units. Profile-overridable
# (Gaming forces 0).
OPT_DEADZONE = 2

# ── Battery — bytes 0x1F..0x23 ──────────────────────────────────────────
BATTERY_VOLTAGE_OFFSET = 0x1F  # u16 LE, mV
BATTERY_CHARGE_OFFSET  = 0x21  # u8 — 0x00 = on battery, 0x20 = full,
                                #       any other non-zero = charging at some rate
BATTERY_CURRENT_OFFSET = 0x22  # u16 LE, raw — only populated when feature
                                #       bit 5 (Rumble) is enabled. Raw value
                                #       must be divided by 100 to get mA
                                #       (per TropicalCyclone driver +
                                #       our 818-s hardware capture, where
                                #       raw 1820 / 100 = 18.2 mA matches
                                #       the JC2's 525 mAh / 20-h spec).
BATTERY_CURRENT_DIVISOR = 100.0  # raw → mA scale factor

# ── Motion Data (IMU) — bytes 0x2A..0x3B of input report 0x05 ──────────
# 18-byte block, present when feature bit 2 (IMU) is enabled in the
# feature mask. We add that bit to the mask only when
# ``settings["imu_enabled"]`` is on (default False — keeps controller
# battery use down for the optical-only path).
#
# Layout (verified against research/ndeadly_switch2/hid_reports.md →
# "Input Report 0x05" → "Motion Data" table):
#
#   0x2A..0x2D   u32 LE  Timestamp (firmware-internal counter)
#   0x2E..0x2F   s16 LE  Temperature (raw — calibration unit unknown)
#   0x30..0x31   s16 LE  Accelerometer X
#   0x32..0x33   s16 LE  Accelerometer Y
#   0x34..0x35   s16 LE  Accelerometer Z
#   0x36..0x37   s16 LE  Gyroscope X
#   0x38..0x39   s16 LE  Gyroscope Y
#   0x3A..0x3B   s16 LE  Gyroscope Z
#
# Earlier versions of docs/ARCHITECTURE.md said "0x30..0x3B" — that range
# is just the accel+gyro sub-block within the larger Motion Data block;
# it omits the leading timestamp+temperature pair.
#
# Single sample per input-report-0x05 packet (block size 0x12 in ndeadly's
# table). For multi-sample motion, input reports 0x07/0x08 carry a larger
# block (size 0x28 = 40 bytes) but with "unknown packed format" — we
# don't subscribe to those.
IMU_OFFSET           = 0x2A
IMU_BLOCK_LEN        = 0x12   # 18 bytes total
IMU_TIMESTAMP_OFFSET = 0x2A   # 4B u32 LE
IMU_TEMP_OFFSET      = 0x2E   # 2B s16 LE
IMU_ACCEL_OFFSET     = 0x30   # 6B = 3 s16 LE (X/Y/Z)
IMU_GYRO_OFFSET      = 0x36   # 6B = 3 s16 LE (X/Y/Z)

# IMU calibration scales. Sources:
#
#   * Accel + gyro + temperature: confirmed by german77 and ndeadly in
#     https://github.com/german77/JoyconDriver/issues/1 (March 2026).
#     Firmware-level constants (not per-controller flash calibration),
#     so they apply to every JC2 unit identically.
#   * Timestamp: german77's docstring says "50k = 1s" but hardware
#     verification on a Joy-Con 2 (R) over BLE on macOS proves the rate
#     is actually 1 MHz (1µs per tick). At 30ms BLE intervals we observe
#     ts deltas of 30000, which is 30000 / 0.030s = 1_000_000 Hz, not
#     50000. german77's value may have been measured over USB or with a
#     different controller revision; we go with the empirically-verified
#     1 MHz here.
#
#   timestamp: 1_000_000 ticks per second (1µs per tick) — VERIFIED ON HW
#   temperature: degC = 25 + raw / 127                 — verified
#   accel: 4096 raw counts = 1 G                       — verified (idle √Σ ≈ 1.0 G)
#   gyro:  48000 raw counts = 360 degrees              — verified (positive when rotating CCW about each axis)
# ── Magnetometer — bytes 0x19..0x1E of input report 0x05 ────────────────
# Three s16 LE values: X, Y, Z. Activated via feature bit 7 (0x80,
# FEATURE_MAGNETOMETER). Already on by default since FEATURE_MASK_DEFAULT
# is 0xFF (everything) in v0.6.0+. Layout per ndeadly hid_reports.md
# table for input report 0x05 + cross-checked against TropicalCyclone's
# driver (data[25:27], [27:29], [29:31] LE signed). Raw counts —
# Nintendo doesn't expose a public calibration constant, so absolute
# orientation requires per-controller calibration which is gated
# behind the unimplemented SPI flash read path.
MAG_OFFSET    = 0x19
MAG_BLOCK_LEN = 0x06   # 6 bytes total (3 × s16)

# Tier S — hardware-verified on a JC2 (R) over BLE on macOS. Accel,
# gyro and temperature constants are firmware-defined and cross-confirmed
# by german77 + ndeadly in JoyconDriver issue #1; the timestamp Hz value
# was empirically corrected from the 50 kHz figure in that issue to the
# 1 MHz value measured on our hardware (see note above).
IMU_TIMESTAMP_HZ          = 1_000_000.0
IMU_TEMP_OFFSET_DEG_C     = 25.0
IMU_TEMP_DIVISOR          = 127.0
IMU_ACCEL_COUNTS_PER_G    = 4096.0
IMU_GYRO_COUNTS_PER_360   = 48_000.0
