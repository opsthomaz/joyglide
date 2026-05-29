# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for ``parser.mouse_optical`` — the optical-sensor (cursor) parser.

This is the flagship parser: it converts the absolute X/Y optical readings
in input report 0x05 (bytes 0x10..0x17) into per-axis deltas and feeds them
either to the pump accumulator (normal profiles) or straight to the OS mouse
(gaming bypass). The logic that a regression would silently break:

  * Lift-off / no-data sentinel — firmware zeros bytes 0x14..0x17 when the
    sensor isn't on a surface (ndeadly + german77).
  * Delta computation via ``u16_delta.delta_u16`` (wrap-around safe).
  * Deadzone gate, per-profile acceleration multiplier, sensitivity scale.
  * Gaming bypass — emits directly, never touches the accumulator.
  * Pause gate — updates last position but does NOT accumulate.

Cross-references:
  * ``parser/mouse_optical.py`` — the parser under test
  * ``parser.constants`` — the OPT_* offsets it reads
  * CLAUDE.md §5 — new parsers require a property test + exact-value test
"""
import math
from unittest.mock import MagicMock

from hypothesis import given
from hypothesis import strategies as st

import parser.mouse_optical
from parser.constants import (
    OPT_LIFTOFF_OFFSET,
    OPT_SURFACE_OFFSET,
    OPT_X_OFFSET,
    OPT_Y_OFFSET,
)

_PACKET_LEN = OPT_LIFTOFF_OFFSET + 2  # 0x18 — minimum length the parser reads


def _state(prev=(None, None)):
    """Minimum-viable state object for the optical parser.

    The parser reads ``state.paused`` / ``state.last_mouse_pos``, calls
    ``state.start_pump()`` and ``state.input_simulator.mouse_move()``, and
    mutates the ``_dx_accum`` / ``_dy_accum`` / motion-prediction fields.
    MagicMock absorbs the method calls; the numeric fields are seeded so
    ``+=`` works.
    """
    s = MagicMock()
    s.paused = False
    s.last_mouse_pos = prev
    s._dx_accum = 0.0
    s._dy_accum = 0.0
    s._last_motion_ts = 0.0
    s._pred_vx = 0.0
    s._pred_vy = 0.0
    s._motion_seq = 0
    return s


def _packet(x_raw=0, y_raw=0, surface=1, liftoff=0) -> bytes:
    """Build a 0x18-byte input-report slice carrying the given optical
    values. ``surface`` defaults to 1 so the lift-off sentinel (all four
    of bytes 0x14..0x17 zero) is NOT triggered."""
    buf = bytearray(_PACKET_LEN)
    buf[OPT_X_OFFSET]       = x_raw & 0xFF
    buf[OPT_X_OFFSET + 1]   = (x_raw >> 8) & 0xFF
    buf[OPT_Y_OFFSET]       = y_raw & 0xFF
    buf[OPT_Y_OFFSET + 1]   = (y_raw >> 8) & 0xFF
    buf[OPT_SURFACE_OFFSET]     = surface & 0xFF
    buf[OPT_SURFACE_OFFSET + 1] = (surface >> 8) & 0xFF
    buf[OPT_LIFTOFF_OFFSET]     = liftoff & 0xFF
    buf[OPT_LIFTOFF_OFFSET + 1] = (liftoff >> 8) & 0xFF
    return bytes(buf)


def _set_profile(monkeypatch, profile, *, disable_accel=True, sensitivity=1.0, deadzone=2):
    monkeypatch.setitem(parser.mouse_optical.settings, "profile", profile)
    monkeypatch.setitem(parser.mouse_optical.settings, "disable_acceleration", disable_accel)
    monkeypatch.setitem(parser.mouse_optical.settings, "sensitivity", sensitivity)
    monkeypatch.setitem(parser.mouse_optical.settings, "deadzone", deadzone)


class TestLiftOffSentinel:
    """Bytes 0x14..0x17 all zero = sensor off a surface → complete no-op."""

    def test_all_zero_status_block_is_noop(self):
        state = _state(prev=(100, 100))
        # surface=0, liftoff=0 → the OR of 0x14..0x17 is zero → early return.
        parser.mouse_optical.parse(state, _packet(500, 600, surface=0, liftoff=0))
        assert state._dx_accum == 0.0
        assert state._dy_accum == 0.0
        # Returned before updating position, so prev is untouched.
        assert state.last_mouse_pos == (100, 100)

    def test_nonzero_surface_byte_alone_is_live(self, monkeypatch):
        """A fresh sample with only the surface field non-zero must still
        be processed (not mistaken for lift-off)."""
        _set_profile(monkeypatch, "dynamic")
        state = _state(prev=(100, 100))
        parser.mouse_optical.parse(state, _packet(110, 100, surface=1, liftoff=0))
        assert state._dx_accum == 10.0


class TestShortPacket:
    def test_packet_shorter_than_0x18_is_noop(self):
        state = _state(prev=(100, 100))
        parser.mouse_optical.parse(state, b"\x00" * (_PACKET_LEN - 1))
        assert state._dx_accum == 0.0
        assert state.last_mouse_pos == (100, 100)


class TestFirstPacket:
    def test_first_packet_only_stashes_position(self):
        """With no previous position (None, None), the first packet seeds
        last_mouse_pos but produces no delta."""
        state = _state(prev=(None, None))
        parser.mouse_optical.parse(state, _packet(300, 400))
        assert state._dx_accum == 0.0
        assert state._dy_accum == 0.0
        assert state.last_mouse_pos == (300, 400)


class TestPauseGate:
    def test_paused_updates_position_but_does_not_accumulate(self):
        """Paused: update last_mouse_pos (so post-resume delta is computed
        from the latest position, no jump), but never accumulate."""
        state = _state(prev=(1, 2))
        state.paused = True
        parser.mouse_optical.parse(state, _packet(500, 600))
        assert state.last_mouse_pos == (500, 600)
        assert state._dx_accum == 0.0
        assert state._dy_accum == 0.0


class TestDeltaAccumulation:
    def test_exact_delta_accumulates_in_normal_profile(self, monkeypatch):
        """dynamic + disable_acceleration + sensitivity 1.0 → 1:1 delta into
        the accumulator. prev (100,100) → (110,105) gives dx=10, dy=5."""
        _set_profile(monkeypatch, "dynamic", disable_accel=True, sensitivity=1.0)
        state = _state(prev=(100, 100))
        parser.mouse_optical.parse(state, _packet(110, 105))
        assert state._dx_accum == 10.0
        assert state._dy_accum == 5.0
        assert state.last_mouse_pos == (110, 105)

    def test_sensitivity_scales_the_delta(self, monkeypatch):
        """sensitivity 2.0 doubles the emitted delta (10 → 20)."""
        _set_profile(monkeypatch, "dynamic", disable_accel=True, sensitivity=2.0)
        state = _state(prev=(100, 100))
        parser.mouse_optical.parse(state, _packet(110, 100))
        assert state._dx_accum == 20.0

    def test_delta_inside_deadzone_is_suppressed(self, monkeypatch):
        """deadzone=2 → a delta of 1 raw unit is swallowed; nothing
        accumulates, but the position still advances."""
        _set_profile(monkeypatch, "dynamic", deadzone=2)
        state = _state(prev=(100, 100))
        parser.mouse_optical.parse(state, _packet(101, 100))  # dx=1 ≤ deadzone
        assert state._dx_accum == 0.0
        assert state.last_mouse_pos == (101, 100)

    def test_delta_uses_wraparound_safe_u16_delta(self, monkeypatch):
        """A wrap from 65530 → 5 is a +11 delta, not -65525. Pins that the
        parser routes through ``delta_u16`` rather than a naive subtraction."""
        _set_profile(monkeypatch, "dynamic", disable_accel=True, sensitivity=1.0)
        state = _state(prev=(65530, 100))
        parser.mouse_optical.parse(state, _packet(5, 100))
        assert state._dx_accum == 11.0


class TestGamingBypass:
    def test_gaming_emits_directly_and_skips_accumulator(self, monkeypatch):
        """Gaming profile: deadzone forced to 0, multiplier 1.0, and the
        delta is posted straight to the OS mouse — the pump accumulator
        stays empty."""
        _set_profile(monkeypatch, "gaming", disable_accel=True, sensitivity=1.0)
        state = _state(prev=(100, 100))
        parser.mouse_optical.parse(state, _packet(110, 105))
        state.input_simulator.mouse_move.assert_called_once_with(10.0, 5.0)
        assert state._dx_accum == 0.0
        assert state._dy_accum == 0.0


class TestProperty:
    @given(
        data=st.binary(min_size=_PACKET_LEN, max_size=64),
        prev_x=st.integers(min_value=0, max_value=0xFFFF),
        prev_y=st.integers(min_value=0, max_value=0xFFFF),
    )
    def test_parse_never_crashes_and_accumulators_stay_finite(self, data, prev_x, prev_y):
        """Arbitrary well-formed-length packets and any previous position
        must never raise and never push the accumulators to inf/nan."""
        state = _state(prev=(prev_x, prev_y))
        parser.mouse_optical.parse(state, data)
        assert math.isfinite(state._dx_accum)
        assert math.isfinite(state._dy_accum)

    @given(data=st.binary(max_size=_PACKET_LEN - 1))
    def test_runt_packets_never_crash(self, data):
        """Any packet shorter than the readable window is a safe no-op."""
        state = _state(prev=(100, 100))
        parser.mouse_optical.parse(state, data)
        assert state._dx_accum == 0.0
