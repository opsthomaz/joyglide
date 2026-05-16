# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact-value regression tests for ``parser.battery``.

Why this file exists:
    The existing property tests in ``tests/test_property.py`` prove
    *shape* — that ``battery_pct`` lands in [0, 100], that throttling
    happens, that implausible voltages are skipped. Cosmic-ray mutation
    testing on ``parser/battery.py`` (2026-05-16) revealed that those
    shape assertions let 53/224 (24%) of AST mutations survive: most of
    the surviving mutants either (a) produced math that got absorbed by
    the ``max(0, min(100, ...))`` clamp, or (b) altered numbers inside
    the throttle/filter that the existing tests didn't pin precisely.

    These tests pin SPECIFIC voltage→percent pairs, exact filter
    boundaries, exact charge-byte semantics, exact current divisor, and
    the exact throttle-window cutoff. They convert "shape" assertions
    into "truth" assertions for the Tier-S battery parsing claims.

    Re-run ``./packaging/run_cosmic_ray.sh parser/battery.py`` after any
    change to ``parser/battery.py`` to verify the mutation score does
    not regress below ~95%.
"""
from types import SimpleNamespace
from unittest.mock import patch

import parser.battery as battery
from parser.constants import (
    BATTERY_CHARGE_OFFSET,
    BATTERY_CURRENT_OFFSET,
    BATTERY_VOLTAGE_OFFSET,
)


def _state():
    """Minimal state object that ``battery.parse`` reads from and writes to.

    ``_battery_last_ts = 0.0`` ensures the throttle gate is open by default
    (any reasonable ``time.monotonic()`` value will be ≥1.0 s ahead, so the
    throttle does not fire). Tests that exercise the throttle override
    ``_battery_last_ts`` explicitly.
    """
    return SimpleNamespace(
        _battery_last_ts=0.0,
        battery_mv=None,
        battery_pct=None,
        battery_full=False,
        battery_charging=False,
        battery_current_ma=None,
    )


def _packet(mv: int, charge_byte: int = 0x00, current_raw: int = 0) -> bytes:
    """Build a minimal input report 0x05 with battery fields populated.

    Packet is exactly ``BATTERY_CURRENT_OFFSET + 2`` bytes (just enough
    to include the current field). Voltage at 0x1F-0x20 LE u16, charge
    byte at 0x21, current at 0x22-0x23 LE u16.
    """
    pkt = bytearray(BATTERY_CURRENT_OFFSET + 2)
    pkt[BATTERY_VOLTAGE_OFFSET] = mv & 0xFF
    pkt[BATTERY_VOLTAGE_OFFSET + 1] = (mv >> 8) & 0xFF
    pkt[BATTERY_CHARGE_OFFSET] = charge_byte & 0xFF
    pkt[BATTERY_CURRENT_OFFSET] = current_raw & 0xFF
    pkt[BATTERY_CURRENT_OFFSET + 1] = (current_raw >> 8) & 0xFF
    return bytes(pkt)


# ───────────────────────── Voltage → percent ──────────────────────────


class TestVoltageToPercentExactMapping:
    """Pin the ``round((mv - 3300) * 100 / 900)`` mapping at specific
    points. Kills cosmic-ray survivors at line 53 where operator swaps
    (Sub→Add, Mul→Div, etc.) produced values absorbed by the clamp."""

    def test_3300_mv_exactly_0_percent(self):
        s = _state()
        with patch("parser.battery.time.monotonic", return_value=100.0):
            battery.parse(s, _packet(3300))
        assert s.battery_mv == 3300
        assert s.battery_pct == 0

    def test_4200_mv_exactly_100_percent(self):
        s = _state()
        with patch("parser.battery.time.monotonic", return_value=100.0):
            battery.parse(s, _packet(4200))
        assert s.battery_pct == 100

    def test_3750_mv_exactly_50_percent_round_midpoint(self):
        """(3750 - 3300) * 100 / 900 = 50."""
        s = _state()
        with patch("parser.battery.time.monotonic", return_value=100.0):
            battery.parse(s, _packet(3750))
        assert s.battery_pct == 50

    def test_3600_mv_yields_33_percent_non_round(self):
        """Non-round midpoint: (3600 - 3300) * 100 / 900 = 33.333…
        ``round()`` in Python uses banker's rounding for exact-half
        cases, but 33.333… is unambiguously closer to 33 than 34."""
        s = _state()
        with patch("parser.battery.time.monotonic", return_value=100.0):
            battery.parse(s, _packet(3600))
        assert s.battery_pct == 33

    def test_3900_mv_yields_67_percent_non_round(self):
        """(3900 - 3300) * 100 / 900 = 66.666… → round to 67."""
        s = _state()
        with patch("parser.battery.time.monotonic", return_value=100.0):
            battery.parse(s, _packet(3900))
        assert s.battery_pct == 67

    def test_4500_mv_clamps_to_100(self):
        """Above the linear range: (4500 - 3300) * 100 / 900 = 133.3 →
        ``min(100, ...)`` clamps to 100."""
        s = _state()
        with patch("parser.battery.time.monotonic", return_value=100.0):
            battery.parse(s, _packet(4500))
        assert s.battery_pct == 100

    def test_2800_mv_clamps_to_0(self):
        """Below the linear range: (2800 - 3300) * 100 / 900 = −55.5 →
        ``max(0, ...)`` clamps to 0."""
        s = _state()
        with patch("parser.battery.time.monotonic", return_value=100.0):
            battery.parse(s, _packet(2800))
        assert s.battery_pct == 0


# ─────────────────── Implausibility filter boundaries ───────────────────


class TestImplausibilityFilterExactBoundaries:
    """The filter is ``if mv < 2500 or mv > 5000: return`` — strict ``<``
    and ``>``. Pin the boundary inclusively so a swap to ``<=`` / ``>=``
    is caught."""

    def test_voltage_2499_filtered_state_unchanged(self):
        """2499 < 2500 → filter fires → state.battery_mv stays None."""
        s = _state()
        with patch("parser.battery.time.monotonic", return_value=100.0):
            battery.parse(s, _packet(2499))
        assert s.battery_mv is None
        assert s.battery_pct is None

    def test_voltage_exactly_2500_passes_filter(self):
        """2500 < 2500 is False — filter does NOT fire. Catches the
        ``<`` → ``<=`` mutation."""
        s = _state()
        with patch("parser.battery.time.monotonic", return_value=100.0):
            battery.parse(s, _packet(2500))
        assert s.battery_mv == 2500

    def test_voltage_exactly_5000_passes_filter(self):
        """5000 > 5000 is False — filter does NOT fire. Catches the
        ``>`` → ``>=`` mutation."""
        s = _state()
        with patch("parser.battery.time.monotonic", return_value=100.0):
            battery.parse(s, _packet(5000))
        assert s.battery_mv == 5000

    def test_voltage_5001_filtered_state_unchanged(self):
        """5001 > 5000 → filter fires."""
        s = _state()
        with patch("parser.battery.time.monotonic", return_value=100.0):
            battery.parse(s, _packet(5001))
        assert s.battery_mv is None


# ───────────────────── Charge-byte exact semantics ──────────────────────


class TestChargeByteExactSemantics:
    """Pin the charge-byte → (battery_full, battery_charging) mapping at
    each named value: 0x00, 0x20, 0x34, plus a generic non-zero/non-0x20.
    The existing property test in test_property.py covers this in
    aggregate; these exact-value tests catch mutations that change the
    specific magic numbers 0x00 or 0x20."""

    def test_charge_byte_0x00_means_on_battery(self):
        s = _state()
        with patch("parser.battery.time.monotonic", return_value=100.0):
            battery.parse(s, _packet(3750, charge_byte=0x00))
        assert s.battery_full is False
        assert s.battery_charging is False

    def test_charge_byte_0x20_means_fully_charged(self):
        s = _state()
        with patch("parser.battery.time.monotonic", return_value=100.0):
            battery.parse(s, _packet(3750, charge_byte=0x20))
        assert s.battery_full is True
        assert s.battery_charging is False

    def test_charge_byte_0x34_means_charging_at_rate(self):
        """Per ndeadly hid_reports.md: byte rises and settles on 0x34
        while charging via USB."""
        s = _state()
        with patch("parser.battery.time.monotonic", return_value=100.0):
            battery.parse(s, _packet(3750, charge_byte=0x34))
        assert s.battery_full is False
        assert s.battery_charging is True


# ────────────────────── Battery current divisor ─────────────────────────


class TestBatteryCurrentExactDivisor:
    """Pin ``raw / 100 = mA`` at specific values (Tier-S, hardware
    verified). A regression to ``/ 1`` or ``/ 1000`` is caught by exact
    equality on the float result."""

    def test_raw_1820_yields_18_20_mA_hardware_capture(self):
        """The signature value from the 818-s capture on JC2 (R) at 30%
        battery: raw 1820 → 18.20 mA. Matches the 525 mAh / 20 h spec."""
        s = _state()
        with patch("parser.battery.time.monotonic", return_value=100.0):
            battery.parse(s, _packet(3573, current_raw=1820))
        assert s.battery_current_ma == 18.20

    def test_raw_0_yields_0_mA(self):
        s = _state()
        with patch("parser.battery.time.monotonic", return_value=100.0):
            battery.parse(s, _packet(3750, current_raw=0))
        assert s.battery_current_ma == 0.0

    def test_raw_10000_yields_100_mA(self):
        """A high-current sample (10000 / 100 = 100 mA)."""
        s = _state()
        with patch("parser.battery.time.monotonic", return_value=100.0):
            battery.parse(s, _packet(3750, current_raw=10000))
        assert s.battery_current_ma == 100.0


# ──────────────────── Throttle window exact semantics ───────────────────


class TestThrottleWindowExactlyOneSecond:
    """Pin the throttle at the exact ``now - last_ts < 1.0`` semantic.

    A mutation that swaps ``<`` → ``<=`` shifts the inclusive boundary by
    one tick. A mutation that changes ``1.0`` → ``0`` opens the throttle
    completely; ``1.0`` → ``2.0`` doubles the window. Pinning the boundary
    cleanly catches all three."""

    def test_zero_elapsed_throttled(self):
        """t = last_ts → 0.0 < 1.0 → throttle fires → no update."""
        s = _state()
        s._battery_last_ts = 100.0
        with patch("parser.battery.time.monotonic", return_value=100.0):
            battery.parse(s, _packet(3750))
        assert s.battery_mv is None
        assert s._battery_last_ts == 100.0   # unchanged

    def test_0_999_seconds_elapsed_still_throttled(self):
        """0.999 < 1.0 → throttle fires."""
        s = _state()
        s._battery_last_ts = 100.0
        with patch("parser.battery.time.monotonic", return_value=100.999):
            battery.parse(s, _packet(3750))
        assert s.battery_mv is None

    def test_exactly_1_0_second_elapsed_passes(self):
        """1.0 < 1.0 is False → throttle does NOT fire → update proceeds."""
        s = _state()
        s._battery_last_ts = 100.0
        with patch("parser.battery.time.monotonic", return_value=101.0):
            battery.parse(s, _packet(3750))
        assert s.battery_mv == 3750
        assert s._battery_last_ts == 101.0

    def test_well_past_window_passes(self):
        """Sanity: 5 s elapsed → comfortably past the throttle window."""
        s = _state()
        s._battery_last_ts = 100.0
        with patch("parser.battery.time.monotonic", return_value=105.0):
            battery.parse(s, _packet(3750))
        assert s.battery_mv == 3750


# ──────────────────── Firmware-source branch (skip voltage) ─────────────


class TestFirmwarePctSourceSkipsVoltageBranch:
    """When ``state.battery_pct_source == "firmware"`` (set by
    parser.power_info when subscribed to a side-specific input report),
    the voltage-derived percentage branch MUST be skipped and ownership
    of battery_pct / battery_full / battery_charging deferred entirely
    to parser.power_info.

    Kills the cosmic-ray ``!=`` → ``>=`` mutation on the source check,
    which would otherwise let `"firmware" >= "firmware"` (= True) wrongly
    enter the voltage branch and clobber the firmware value."""

    def test_firmware_source_does_not_overwrite_battery_pct(self):
        s = _state()
        s.battery_pct_source = "firmware"
        s.battery_pct = 42         # pretend power_info set this earlier
        s.battery_full = False
        s.battery_charging = True
        with patch("parser.battery.time.monotonic", return_value=100.0):
            battery.parse(s, _packet(3750, charge_byte=0x20))
        # battery_mv IS updated (informational), but the firmware-owned
        # fields must be untouched — NOT recomputed from voltage / charge.
        assert s.battery_mv == 3750
        assert s.battery_pct == 42                # unchanged
        assert s.battery_full is False            # not overwritten by 0x20→True
        assert s.battery_charging is True         # not overwritten


# ──────────────────────── Current field length gate ─────────────────────


class TestCurrentFieldLengthGate:
    """The current-field gate is ``if len(data) >= BATTERY_CURRENT_OFFSET + 2``.

    Pins the >=-vs-> boundary so a mutation to ``>= OFFSET + 1`` (which
    would index data[OFFSET+1] on a packet that doesn't contain it,
    raising IndexError) is caught."""

    def test_short_packet_one_byte_below_current_field_does_not_raise(self):
        """A 35-byte packet (BATTERY_CURRENT_OFFSET + 1) has the voltage
        + charge fields but NOT the current field. parse() must NOT
        attempt to read data[35] — it should silently skip the current
        update. Mutating the bound to ``>= OFFSET + 1`` (35) would let
        the branch fire and IndexError on data[OFFSET + 1]."""
        s = _state()
        # 35-byte packet: voltage at 0x1F-0x20, charge at 0x21, NO current.
        short = bytearray(BATTERY_CURRENT_OFFSET + 1)
        short[BATTERY_VOLTAGE_OFFSET] = 3750 & 0xFF
        short[BATTERY_VOLTAGE_OFFSET + 1] = (3750 >> 8) & 0xFF
        short[BATTERY_CHARGE_OFFSET] = 0x00
        with patch("parser.battery.time.monotonic", return_value=100.0):
            # If the mutation fires, this raises IndexError.
            battery.parse(s, bytes(short))
        assert s.battery_mv == 3750
        assert s.battery_current_ma is None  # current not parsed
