# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for ``parser.power_info`` — firmware battery level decode.

The Power Info bitfield lives in byte 0x1 of input reports
0x07/0x08/0x09/0x0A. Layout per ndeadly hid_reports.md:

    bit 0      external power
    bit 1      charging
    bits 2..5  battery level (0..9)
    bits 6..7  reserved
"""
import parser.power_info


class _MockState:
    """Minimum state object the power_info parser writes to."""

    def __init__(self):
        self.battery_external_power = False
        self.battery_charging = False
        self.battery_full = False
        self.battery_level_raw = None
        self.battery_pct = None
        self.battery_pct_source = "voltage"
        self._power_info_last_ts = 0.0


def _packet(level: int, charging: bool = False, external: bool = False) -> bytes:
    """Build a minimum-viable side-specific input report with the
    Power Info bitfield at byte 0x1 set as requested. Byte 0x0 is
    the report counter (we don't read it)."""
    pi = ((level & 0x0F) << 2)
    if external: pi |= 0x01
    if charging: pi |= 0x02
    return bytes([0x00, pi])


# ── Throttle gate ───────────────────────────────────────────────────────


def test_power_info_short_packet_skipped():
    """Packets shorter than 2 bytes (no Power Info byte) must be a
    complete no-op — defends against startup races where the side-
    specific report streams a 1-byte counter packet first."""
    state = _MockState()
    parser.power_info.parse(state, b"\x00")
    assert state.battery_pct is None


def test_power_info_throttle_blocks_then_releases(monkeypatch):
    """The parser is throttled to 1 Hz — packets within the window must NOT
    update state, but a packet AFTER the window must. Uses a controlled
    clock (not wall-time) so the window boundary is pinned deterministically,
    not left to flake on real elapsed time."""
    clock = {"t": 1000.0}
    monkeypatch.setattr(parser.power_info.time, "monotonic", lambda: clock["t"])

    state = _MockState()
    parser.power_info.parse(state, _packet(5))
    first_pct = state.battery_pct
    assert first_pct is not None  # level 5 decoded

    # 0.5 s later — inside the 1 Hz window → must be ignored.
    clock["t"] = 1000.5
    parser.power_info.parse(state, _packet(9))
    assert state.battery_pct == first_pct

    # 1.5 s after the first update — window elapsed → must update now.
    clock["t"] = 1001.5
    parser.power_info.parse(state, _packet(9))
    assert state.battery_pct == 100   # level 9 → 100%
    assert state.battery_pct != first_pct


# ── Successful decode + level → pct mapping ─────────────────────────────


def test_power_info_level_mapping():
    """Pin the 0..9 → 0..100% linear endpoints. Round-half-to-even is
    OK for the in-between buckets; only the endpoints (0=0%, 9=100%)
    are required to be exact for "fully charged" and "empty"
    intuition."""
    cases = {
        0: 0,
        1: 11,
        2: 22,
        3: 33,
        4: 44,
        5: 56,
        6: 67,
        7: 78,
        8: 89,
        9: 100,
    }
    for level, expected_pct in cases.items():
        state = _MockState()
        parser.power_info.parse(state, _packet(level))
        assert state.battery_pct == expected_pct, \
            f"level={level} expected pct={expected_pct} got {state.battery_pct}"


def test_power_info_marks_source_as_firmware():
    """When the parser updates state, ``battery_pct_source`` flips to
    "firmware" — parser.battery checks this flag to decide whether
    to overwrite the percentage with its voltage approximation."""
    state = _MockState()
    parser.power_info.parse(state, _packet(5))
    assert state.battery_pct_source == "firmware"


def test_power_info_charging_flag():
    """The charging bit must propagate to ``battery_charging``."""
    state = _MockState()
    parser.power_info.parse(state, _packet(7, charging=True))
    assert state.battery_charging is True


def test_power_info_external_power_flag():
    """The external-power bit must propagate to
    ``battery_external_power``."""
    state = _MockState()
    parser.power_info.parse(state, _packet(7, external=True))
    assert state.battery_external_power is True


def test_power_info_full_requires_charging_or_external():
    """``battery_full`` should fire only at level=9 AND with charging
    or external power present (otherwise the controller is just at
    the top of the discharge curve, not actively replenishing)."""
    # Level 9 alone — running on battery near full but not "full"
    state = _MockState()
    parser.power_info.parse(state, _packet(9))
    assert state.battery_full is False
    # Level 9 with external power — full
    state = _MockState()
    parser.power_info.parse(state, _packet(9, external=True))
    assert state.battery_full is True
    # Level 8 with charging — not full yet
    state = _MockState()
    parser.power_info.parse(state, _packet(8, charging=True))
    assert state.battery_full is False


def test_power_info_invalid_level_rejected():
    """Firmware sometimes returns level=0xF as "level unknown" during
    boot. The parser must not propagate any value above 9 to state —
    leaves the previous reading in place."""
    state = _MockState()
    # Manually craft a byte with bits [2:5] = 0xF (bit pattern 1111).
    bad = bytes([0x00, 0xFC])  # 0b11111100 → ext=0, chg=0, level=0xF
    parser.power_info.parse(state, bad)
    assert state.battery_level_raw is None
    assert state.battery_pct is None
    assert state.battery_pct_source == "voltage"  # not flipped to firmware
