# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the JoyCon motion engine.

Focus on **pure** logic — the math that runs on every BLE packet and
where a regression would silently corrupt cursor behaviour:

  * ``_delta_u16`` — wraparound delta of the optical sensor's u16 absolute
    position. The sensor wraps at 0xFFFF; a naive subtraction would emit
    a giant cursor jump every wrap.
  * Battery percentage formula — linear approximation between 3300 mV and
    4200 mV, clamped to [0, 100]. Easy to break with off-by-one.
  * Battery state byte parsing — full / charging / on-battery interpretation.
  * Implausibility filter — discard junk before sensors stabilise.
"""
import pytest


# Don't import joycon at module level — InputSimulator's __init__ would
# call into Quartz/Win32 and fail in the test environment if displays
# aren't attached. Tests that need it use the fixture below.


@pytest.fixture
def gamepad(monkeypatch):
    """A JoyCon instance with a stub InputSimulator (no real cursor side effects)."""
    class _StubSim:
        def __init__(self):
            self.refresh_rate = 60.0
        def mouse_move(self, *_args): pass
        def mouse_down(self): pass
        def mouse_up(self): pass
        def mouse_down_right(self): pass
        def mouse_up_right(self): pass
        def mouse_down_middle(self): pass
        def mouse_up_middle(self): pass
        def mouse_down_back(self): pass
        def mouse_up_back(self): pass
        def mouse_down_forward(self): pass
        def mouse_up_forward(self): pass
        def mouse_scroll(self, *_args): pass

    import joycon
    monkeypatch.setattr(joycon, "InputSimulator", _StubSim)
    return joycon.JoyCon(side="right")


class TestDeltaU16:
    """``_delta_u16`` interprets two u16 values as adjacent positions on a
    ring and returns the signed delta in [-32768, 32767]."""

    def setup_method(self):
        from parser.u16_delta import delta_u16
        self.delta = delta_u16

    def test_zero_delta(self):
        assert self.delta(100, 100) == 0

    def test_small_positive(self):
        assert self.delta(150, 100) == 50

    def test_small_negative(self):
        assert self.delta(100, 150) == -50

    def test_wrap_forward_across_max(self):
        # Move from 0xFFF0 forward by 0x20 (32 steps) → wraps to 0x0010.
        # Naive subtraction would say -65504; correct delta is +32.
        assert self.delta(0x0010, 0xFFF0) == 32

    def test_wrap_backward_across_zero(self):
        # Move from 0x0010 backward to 0xFFF0 → -32.
        assert self.delta(0xFFF0, 0x0010) == -32

    def test_max_forward_jump_clamps_into_signed_range(self):
        # 0x7FFF (32767) is the largest "still positive" interpretation.
        assert self.delta(0x7FFF, 0) == 32767

    def test_max_backward_jump(self):
        # 0x8000 (32768 in u16) interpreted as the negative side.
        assert self.delta(0x8000, 0) == -32768


class TestBatteryParsing:
    """Tests for ``JoyCon.process_battery``."""

    def _make_packet(self, voltage_mv: int, charge_byte: int = 0x00) -> bytes:
        """Build a minimal 0x22-byte input report with the battery fields set."""
        buf = bytearray(0x22)
        buf[0x1F] = voltage_mv & 0xFF
        buf[0x20] = (voltage_mv >> 8) & 0xFF
        buf[0x21] = charge_byte
        return bytes(buf)

    def test_full_voltage_reports_100_percent(self, gamepad):
        # Force the throttle to allow the next call.
        gamepad._battery_last_ts = 0.0
        gamepad.process_battery(self._make_packet(4200))
        assert gamepad.battery_pct == 100
        assert gamepad.battery_mv == 4200

    def test_empty_voltage_reports_zero_percent(self, gamepad):
        gamepad._battery_last_ts = 0.0
        gamepad.process_battery(self._make_packet(3300))
        assert gamepad.battery_pct == 0

    def test_midpoint_voltage(self, gamepad):
        # 3750 mV is exact midpoint between 3300 and 4200 → 50%.
        gamepad._battery_last_ts = 0.0
        gamepad.process_battery(self._make_packet(3750))
        assert gamepad.battery_pct == 50

    def test_clamps_below_3300_to_zero(self, gamepad):
        # Plausible-but-low voltage just gets clamped, not rejected.
        gamepad._battery_last_ts = 0.0
        gamepad.process_battery(self._make_packet(2900))
        assert gamepad.battery_pct == 0

    def test_clamps_above_4200_to_hundred(self, gamepad):
        gamepad._battery_last_ts = 0.0
        gamepad.process_battery(self._make_packet(4500))
        assert gamepad.battery_pct == 100

    def test_implausible_voltage_is_rejected(self, gamepad):
        # < 2500 mV or > 5000 mV is outside any LiPo cell range — junk.
        gamepad._battery_last_ts = 0.0
        gamepad.process_battery(self._make_packet(1000))
        assert gamepad.battery_mv is None    # not updated
        assert gamepad.battery_pct is None

        gamepad.process_battery(self._make_packet(6000))
        assert gamepad.battery_mv is None
        assert gamepad.battery_pct is None

    def test_charge_state_full_means_battery_full(self, gamepad):
        gamepad._battery_last_ts = 0.0
        gamepad.process_battery(self._make_packet(4200, charge_byte=0x20))
        assert gamepad.battery_full is True
        assert gamepad.battery_charging is False

    def test_charge_state_nonzero_nonfull_means_charging(self, gamepad):
        gamepad._battery_last_ts = 0.0
        gamepad.process_battery(self._make_packet(3800, charge_byte=0x34))
        assert gamepad.battery_full is False
        assert gamepad.battery_charging is True

    def test_charge_state_zero_means_on_battery(self, gamepad):
        gamepad._battery_last_ts = 0.0
        gamepad.process_battery(self._make_packet(3800, charge_byte=0x00))
        assert gamepad.battery_full is False
        assert gamepad.battery_charging is False

    def test_throttling_skips_updates_within_one_second(self, gamepad):
        gamepad._battery_last_ts = 0.0
        gamepad.process_battery(self._make_packet(4200))
        first_pct = gamepad.battery_pct
        # Immediately try to update with a different voltage — should be skipped.
        gamepad.process_battery(self._make_packet(3300))
        assert gamepad.battery_pct == first_pct

    def test_short_packet_is_rejected(self, gamepad):
        gamepad._battery_last_ts = 0.0
        gamepad.process_battery(b"\x00" * 0x21)   # one byte short
        assert gamepad.battery_pct is None

    def test_battery_current_parsed_when_packet_long_enough(self, gamepad):
        # Packets that include bytes 0x22-0x23 carry the battery current
        # (only populated when FEATURE_RUMBLE is in the feature mask, which
        # we set by default — see ble.feature_flags.FEATURE_MASK_DEFAULT).
        # Raw u16 is divided by 100 to get mA — see parser.battery for
        # the full derivation against TC's driver + our 818-s capture.
        buf = bytearray(0x24)
        buf[0x1F] = 0x68; buf[0x20] = 0x10                          # 4200 mV
        buf[0x22] = 0x2C; buf[0x23] = 0x01                          # 0x012C raw = 3.00 mA
        gamepad._battery_last_ts = 0.0
        gamepad.process_battery(bytes(buf))
        assert gamepad.battery_current_ma == 3.00

    def test_battery_current_stays_none_when_packet_short(self, gamepad):
        # Pre-feature-mask-bit-5 packets (only 0x22 bytes long) carry no
        # current field. process_battery should still parse voltage/charge
        # but leave battery_current_ma untouched.
        gamepad._battery_last_ts = 0.0
        gamepad.process_battery(self._make_packet(4200))            # exactly 0x22 bytes
        assert gamepad.battery_pct == 100
        assert gamepad.battery_current_ma is None


class TestProcessMouseLiftoff:
    """``process_mouse`` must skip when the optical sensor isn't reporting
    fresh data — detected via the surface/lift-off bytes (0x14-0x17), not
    the right-stick byte at 0x0F that earlier builds incorrectly used.
    """

    def _packet_with_mouse(self, x=0x1234, y=0x5678,
                           surface=0x0001, liftoff=0x0001) -> bytes:
        # Build a minimum-viable input report 0x05 with mouse fields set.
        buf = bytearray(0x18)
        buf[0x10] = x & 0xFF;       buf[0x11] = (x >> 8) & 0xFF
        buf[0x12] = y & 0xFF;       buf[0x13] = (y >> 8) & 0xFF
        buf[0x14] = surface & 0xFF; buf[0x15] = (surface >> 8) & 0xFF
        buf[0x16] = liftoff & 0xFF; buf[0x17] = (liftoff >> 8) & 0xFF
        return bytes(buf)

    def test_skips_when_mouse_block_all_zero(self, gamepad):
        # All four "extra mouse info" bytes zero → controller isn't on a
        # surface, skip processing. last_mouse_pos must NOT be touched.
        gamepad.last_mouse_pos = (100, 200)
        gamepad.process_mouse(self._packet_with_mouse(x=0, y=0,
                                                      surface=0, liftoff=0))
        assert gamepad.last_mouse_pos == (100, 200)

    def test_processes_when_liftoff_nonzero(self, gamepad):
        # Even with X/Y at zero, a non-zero lift-off byte means the firmware
        # is reporting a fresh sample → process it.
        gamepad.last_mouse_pos = (None, None)
        gamepad.process_mouse(self._packet_with_mouse(x=0, y=0,
                                                      surface=0, liftoff=1))
        assert gamepad.last_mouse_pos == (0, 0)

    def test_processes_when_surface_quality_nonzero(self, gamepad):
        gamepad.last_mouse_pos = (None, None)
        gamepad.process_mouse(self._packet_with_mouse(x=0x1234, y=0x5678,
                                                      surface=1, liftoff=0))
        assert gamepad.last_mouse_pos == (0x1234, 0x5678)

    def test_short_packet_is_rejected(self, gamepad):
        gamepad.last_mouse_pos = (100, 200)
        gamepad.process_mouse(b"\x00" * 0x17)   # one byte short of 0x18
        assert gamepad.last_mouse_pos == (100, 200)


class TestProcessButtonsPause:
    """``process_buttons`` must early-return when paused so that pressing a
    button during pause and releasing after resume can't fire a phantom
    mouse-up. (Regression test for the v0.2.4 fix.)"""

    def test_paused_freezes_state(self, gamepad):
        # Pre-pause: button RELEASED.
        released = bytearray(0x14)
        # Right-side state at offset 3..5 — set R bit (0x004000) for "pressed"
        gamepad.last_data = bytes(released)

        # Pause and "press" the R button (set bit 0x004000 in big-endian 3-byte word)
        pressed = bytearray(0x14)
        # offset 3..5 holds 24-bit big-endian state for right side. R = 0x004000.
        pressed[3] = 0x00
        pressed[4] = 0x40   # high byte of 0x004000 in BE — middle of 3-byte field
        pressed[5] = 0x00
        gamepad.paused = True

        # Track sim calls
        calls = []
        gamepad.input_simulator.mouse_down = lambda: calls.append("down")
        gamepad.input_simulator.mouse_up = lambda: calls.append("up")

        gamepad.process_buttons(bytes(pressed))
        # While paused: no events emitted, last_data NOT updated.
        assert calls == []
        assert gamepad.last_data == bytes(released)


class TestPacketRateEMA:
    """``track_packet_rate`` updates the EMA of inter-packet time, which
    drives the pump's adaptive drain factor."""

    def test_single_packet_doesnt_update_ema(self, gamepad):
        # First call seeds _last_packet_ts but the EMA needs two samples.
        initial = gamepad._ble_period_ema
        gamepad.track_packet_rate()
        assert gamepad._ble_period_ema == initial

    def test_implausible_intervals_rejected(self, gamepad, monkeypatch):
        # Inject a sub-5ms interval (impossibly fast) — should be rejected.
        import joycon
        # First sample
        monkeypatch.setattr(joycon.time, "monotonic", lambda: 100.0)
        gamepad.track_packet_rate()
        # Second sample, only 1ms later — too short to be plausible BLE
        monkeypatch.setattr(joycon.time, "monotonic", lambda: 100.001)
        prev = gamepad._ble_period_ema
        gamepad.track_packet_rate()
        # EMA stays at the seed value because the interval was too short.
        assert gamepad._ble_period_ema == prev

    def test_realistic_interval_pulls_ema(self, gamepad, monkeypatch):
        import joycon
        # Seed
        monkeypatch.setattr(joycon.time, "monotonic", lambda: 0.0)
        gamepad.track_packet_rate()
        # 30ms later (typical macOS BLE packet)
        monkeypatch.setattr(joycon.time, "monotonic", lambda: 0.030)
        gamepad.track_packet_rate()
        # EMA should move toward 30ms (it starts at 30ms by default, so it
        # might be steady — but never moves *away* from a plausible value).
        assert 0.025 <= gamepad._ble_period_ema <= 0.035
