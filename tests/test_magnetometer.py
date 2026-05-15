# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for ``parser.magnetometer`` — 3-axis raw magnetometer decoder.

Layout sourced from research/ndeadly_switch2/hid_reports.md and
cross-validated against TropicalCyclone/switch2-controller-driver
(both agree on offset 0x19, 6 bytes, 3 × s16 LE).
"""
import struct

import parser.magnetometer
from parser.constants import MAG_BLOCK_LEN, MAG_OFFSET


class _MockState:
    """Minimum state object the magnetometer parser writes to."""

    def __init__(self):
        self.magnetometer = None


def _packet_with_mag(mx: int, my: int, mz: int) -> bytes:
    """Build a packet whose offset 0x19..0x1E carries the given mag
    values. Bytes 0..0x18 are zero (we don't read them in this test)."""
    buf = bytearray(MAG_OFFSET + MAG_BLOCK_LEN)
    struct.pack_into("<3h", buf, MAG_OFFSET, mx, my, mz)
    return bytes(buf)


# ── No-op gates ──────────────────────────────────────────────────────────


def test_mag_disabled_by_default(monkeypatch):
    """When ``magnetometer_enabled`` is False (default), parse must
    NOT touch state — even with a perfectly valid packet. Hot-path
    early return that protects the IMU-only / optical-only user from
    paying any cost for a feature they didn't ask for."""
    monkeypatch.setitem(parser.magnetometer.settings, "magnetometer_enabled", False)
    state = _MockState()
    parser.magnetometer.parse(state, _packet_with_mag(100, 200, 300))
    assert state.magnetometer is None


def test_mag_short_packet_skipped(monkeypatch):
    """Packets too short to contain the 6-byte mag block must be a
    complete no-op (no partial decode, no exception)."""
    monkeypatch.setitem(parser.magnetometer.settings, "magnetometer_enabled", True)
    state = _MockState()
    parser.magnetometer.parse(state, b"\x00" * MAG_OFFSET)              # exactly 0x19, no mag bytes
    assert state.magnetometer is None
    parser.magnetometer.parse(state, b"\x00" * (MAG_OFFSET + 3))        # mid-block
    assert state.magnetometer is None


# ── Successful decode + signedness ──────────────────────────────────────


def test_mag_signed_decode(monkeypatch):
    """Verify s16 signedness — negative raw values must decode to
    negative ints, not interpreted as u16. Catches a regression to
    `<3H>` (unsigned) which would silently flip negative-field
    readings to ~65000."""
    monkeypatch.setitem(parser.magnetometer.settings, "magnetometer_enabled", True)
    state = _MockState()
    parser.magnetometer.parse(state, _packet_with_mag(-100, 200, -300))
    assert state.magnetometer == (-100, 200, -300)


def test_mag_full_range(monkeypatch):
    """Pin the s16 boundaries — max positive (32767) and max negative
    (-32768) must round-trip cleanly through the parser."""
    monkeypatch.setitem(parser.magnetometer.settings, "magnetometer_enabled", True)
    state = _MockState()
    parser.magnetometer.parse(state, _packet_with_mag(32767, -32768, 0))
    assert state.magnetometer == (32767, -32768, 0)


def test_mag_zero_packet_decodes_zero(monkeypatch):
    """All-zero magnetometer bytes must decode to (0, 0, 0). Important
    boundary because a u16 field-mismatch would produce something
    like 65536-shaped junk instead of 0."""
    monkeypatch.setitem(parser.magnetometer.settings, "magnetometer_enabled", True)
    state = _MockState()
    parser.magnetometer.parse(state, _packet_with_mag(0, 0, 0))
    assert state.magnetometer == (0, 0, 0)


def test_mag_diagnostic_logging(monkeypatch):
    """When ``magnetometer_dump_raw`` is on, every parsed packet must
    log the decoded values."""
    import logging
    monkeypatch.setitem(parser.magnetometer.settings, "magnetometer_enabled", True)
    monkeypatch.setitem(parser.magnetometer.settings, "magnetometer_dump_raw", True)

    captured: list[str] = []

    class _ListHandler(logging.Handler):
        def emit(self, record):
            captured.append(self.format(record))

    handler = _ListHandler(level=logging.INFO)
    handler.setFormatter(logging.Formatter("%(message)s"))
    parser.magnetometer.log.addHandler(handler)
    try:
        state = _MockState()
        parser.magnetometer.parse(state, _packet_with_mag(123, -456, 789))
    finally:
        parser.magnetometer.log.removeHandler(handler)

    assert any("mag" in m for m in captured)
    assert any("+123" in m for m in captured)
    assert any("-456" in m for m in captured)
    assert any("+789" in m for m in captured)
