# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for ``parser.imu`` — IMU (gyro + accel) decoder.

Layout and calibration scales sourced from:
  * research/ndeadly_switch2/hid_reports.md (offset table for input
    report 0x05 → Motion Data)
  * github.com/german77/JoyconDriver#1 (firmware-level conversion
    constants for handle 0x000A)

Both upstream sources agree:
  - Block at 0x2A, 18 bytes total
  - Layout: timestamp (u32) + temp (s16) + accel xyz (s16×3) + gyro xyz (s16×3)
  - Scales: temp = 25 + raw/127, accel = raw/4096 G, gyro = raw/48000*360 deg
"""
import struct

import parser.imu
from parser.constants import IMU_BLOCK_LEN, IMU_OFFSET


def test_imu_timestamp_hz_pinned_at_1_mhz():
    """CLAUDE.md §4 (Tier S, hardware-verified): IMU timestamp ticks at
    1 MHz (1 µs/tick). A 'correction' back to 50 kHz — the value cited
    in github.com/german77/JoyconDriver#1 — would be a Tier-D regression
    against measured BLE-on-macOS behaviour (Δts = 30000 across 30ms
    packets = 1 MHz, not 50 kHz).
    """
    from parser.constants import IMU_TIMESTAMP_HZ
    assert IMU_TIMESTAMP_HZ == 1_000_000.0


class _MockState:
    """Minimum state object the IMU parser writes to. Mirrors the
    ``JoyCon`` attrs that ``parser.imu`` mutates."""

    def __init__(self):
        self.imu_timestamp = None
        self.imu_temperature = None
        self.imu_temperature_c = None
        self.imu_accel = None
        self.imu_accel_g = None
        self.imu_gyro = None
        self.imu_gyro_deg = None


def _packet_with_imu(ts: int, temp: int,
                     ax: int, ay: int, az: int,
                     gx: int, gy: int, gz: int) -> bytes:
    """Construct a packet whose offset 0x2A..0x3B carries the given IMU
    values. Bytes 0..0x29 are zero (we don't read them in this test)."""
    buf = bytearray(IMU_OFFSET + IMU_BLOCK_LEN)
    struct.pack_into("<Ih3h3h", buf, IMU_OFFSET,
                      ts, temp, ax, ay, az, gx, gy, gz)
    return bytes(buf)


# ── No-op gates ──────────────────────────────────────────────────────────


def test_imu_disabled_by_default(monkeypatch):
    """When ``imu_enabled`` is False (default), parse must NOT touch
    state — even with a perfectly valid packet. This is the hot-path
    early return that protects the optical-only user from paying any
    cost for a feature they didn't ask for."""
    monkeypatch.setitem(parser.imu.settings, "imu_enabled", False)
    state = _MockState()
    parser.imu.parse(state, _packet_with_imu(1234, 0, 0, 0, 4096, 0, 0, 0))
    assert state.imu_timestamp is None
    assert state.imu_accel is None
    assert state.imu_gyro is None


def test_imu_short_packet_skipped(monkeypatch):
    """Packets too short to contain the full 18-byte block must be a
    complete no-op (no partial decode, no exception). Defends against
    feature-negotiation race on reconnect, where the controller may
    send one or two small packets before fully honoring our mask."""
    monkeypatch.setitem(parser.imu.settings, "imu_enabled", True)
    state = _MockState()
    # 0x2A bytes — exactly one byte short of the IMU block start, so
    # the decode would underflow.
    parser.imu.parse(state, b"\x00" * IMU_OFFSET)
    assert state.imu_timestamp is None
    parser.imu.parse(state, b"\x00" * (IMU_OFFSET + 5))  # mid-block
    assert state.imu_timestamp is None


# ── Successful decode + calibration ─────────────────────────────────────


def test_imu_raw_decode_signed(monkeypatch):
    """Verify s16 signedness — negative raw values must decode to
    negative ints, not interpreted as u16. Catches a regression to
    `<I h 3H 3H>` (unsigned axes) which would silently flip negative
    motion to ~65000."""
    monkeypatch.setitem(parser.imu.settings, "imu_enabled", True)
    state = _MockState()
    pkt = _packet_with_imu(0, -50, -100, 200, -300, 400, -500, 600)
    parser.imu.parse(state, pkt)
    assert state.imu_temperature == -50
    assert state.imu_accel == (-100, 200, -300)
    assert state.imu_gyro == (400, -500, 600)


def test_imu_temperature_calibration(monkeypatch):
    """Temperature: degC = 25 + raw/127. Pin the constants explicitly —
    a mutation to either the offset (25) or divisor (127) would
    produce a wrong physical reading."""
    monkeypatch.setitem(parser.imu.settings, "imu_enabled", True)
    state = _MockState()
    # raw = 0 → 25.0 degC exactly.
    parser.imu.parse(state, _packet_with_imu(0, 0, 0, 0, 0, 0, 0, 0))
    assert state.imu_temperature_c == 25.0
    # raw = 127 → 25 + 127/127 = 26.0 degC.
    parser.imu.parse(state, _packet_with_imu(0, 127, 0, 0, 0, 0, 0, 0))
    assert state.imu_temperature_c == 26.0
    # raw = -127 → 24.0 degC.
    parser.imu.parse(state, _packet_with_imu(0, -127, 0, 0, 0, 0, 0, 0))
    assert state.imu_temperature_c == 24.0


def test_imu_accel_calibration_one_g(monkeypatch):
    """Accel: 4096 raw counts = 1 G. A controller lying flat on a desk
    should report ~+1 G on Z (or whichever axis is "up" per Nintendo's
    convention). Pin the divisor explicitly."""
    monkeypatch.setitem(parser.imu.settings, "imu_enabled", True)
    state = _MockState()
    parser.imu.parse(state, _packet_with_imu(0, 0, 0, 0, 4096, 0, 0, 0))
    assert state.imu_accel_g is not None
    assert state.imu_accel_g[0] == 0.0
    assert state.imu_accel_g[1] == 0.0
    assert state.imu_accel_g[2] == 1.0  # exactly one G


def test_imu_gyro_calibration_half_rotation(monkeypatch):
    """Gyro: 48000 raw counts = 360 degrees. raw=24000 → 180 deg,
    raw=12000 → 90 deg. Pins the scale factor.

    Note: 48000 itself is the *scale constant*, not a value you'd ever
    see on the wire — gyro raw is s16 (max 32767), so the largest
    physical-per-sample reading is ~245.75°. We pin two interior values
    to verify the linear mapping; a `48` typo'd to `4800` would silently
    multiply gyro by 10 and fail both assertions."""
    monkeypatch.setitem(parser.imu.settings, "imu_enabled", True)
    state = _MockState()
    parser.imu.parse(state, _packet_with_imu(0, 0, 0, 0, 0, 24000, 0, 0))
    assert state.imu_gyro_deg is not None
    assert state.imu_gyro_deg[0] == 180.0
    parser.imu.parse(state, _packet_with_imu(0, 0, 0, 0, 0, 12000, 0, 0))
    assert state.imu_gyro_deg[0] == 90.0
    # Negative — pins the signedness across the calibration scaling.
    parser.imu.parse(state, _packet_with_imu(0, 0, 0, 0, 0, -24000, 0, 0))
    assert state.imu_gyro_deg[0] == -180.0


def test_imu_timestamp_decoded_as_unsigned(monkeypatch):
    """Timestamp is u32 LE. A large value (> 2^31) must decode as a
    large positive number, not a negative s32."""
    monkeypatch.setitem(parser.imu.settings, "imu_enabled", True)
    state = _MockState()
    parser.imu.parse(state, _packet_with_imu(2**32 - 1, 0, 0, 0, 0, 0, 0, 0))
    assert state.imu_timestamp == 2**32 - 1


def test_imu_diagnostic_logging(monkeypatch):
    """When ``imu_dump_raw`` is on, every parsed packet must log the
    decoded values. We attach a list-collector handler since the
    project logger has propagate=False."""
    import logging
    monkeypatch.setitem(parser.imu.settings, "imu_enabled", True)
    monkeypatch.setitem(parser.imu.settings, "imu_dump_raw", True)

    captured: list[str] = []

    class _ListHandler(logging.Handler):
        def emit(self, record):
            captured.append(self.format(record))

    handler = _ListHandler(level=logging.INFO)
    handler.setFormatter(logging.Formatter("%(message)s"))
    parser.imu.log.addHandler(handler)
    try:
        state = _MockState()
        parser.imu.parse(state, _packet_with_imu(42, 0, 0, 0, 4096, 0, 0, 24000))
    finally:
        parser.imu.log.removeHandler(handler)

    assert any("IMU" in m for m in captured)
    # Timestamp 42 should appear in the log.
    assert any("ts=42" in m for m in captured)
    # Z accel = 1.0 G (4096/4096) should appear formatted.
    assert any("+1.000" in m for m in captured)
    # Z gyro = 180.0 deg (24000/48000*360) should appear.
    assert any("+180.00" in m for m in captured)
