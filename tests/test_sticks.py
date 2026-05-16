# SPDX-License-Identifier: GPL-3.0-or-later
"""Direct unit tests for ``parser.sticks``.

The stick parser reads a 3-byte packed-12-bit stick block from input
report 0x05 (offset 13..15 for the right Joy-Con, 10..12 for the left),
normalises to [-1, 1], applies the active profile's curve, and
accumulates into ``state._scroll_x_accum`` / ``_scroll_y_accum``. The
side offset selection and the bounds guard at the top are the two
places where a regression would silently corrupt scroll behaviour.

Cross-references:
  * ``parser/sticks.py`` — the parser under test
  * ``utils.decode_joystick`` — the canonical 12-bit unpacker (tested
    in detail in tests/test_utils.py and tests/test_property.py)
  * Audit finding I2 — bounds check positioned at the top of the function
"""
from unittest.mock import MagicMock


def _state(side: str = "right"):
    """Build a minimum-viable state object for the stick parser.

    The parser reads ``state.is_left``, ``state.paused``, and mutates
    ``state._scroll_x_accum``, ``state._scroll_y_accum``,
    ``state._last_motion_ts``. It also calls ``state.start_pump()``,
    which the MagicMock absorbs as a no-op.
    """
    s = MagicMock()
    s.is_left = (side == "left")
    s.paused = False
    s._scroll_x_accum = 0.0
    s._scroll_y_accum = 0.0
    s._last_motion_ts = 0.0
    return s


def _pack_stick(x_raw: int, y_raw: int) -> bytes:
    """Encode two 12-bit stick values into the 3-byte on-wire packing
    used by Joy-Con input report 0x05."""
    return bytes([
        x_raw & 0xFF,
        ((x_raw >> 8) & 0x0F) | ((y_raw & 0x0F) << 4),
        (y_raw >> 4) & 0xFF,
    ])


def _right_packet(x_raw: int = 0x800, y_raw: int = 0x800) -> bytes:
    """Build a 0x14-byte packet with the right-side stick (offset
    13..15) set to the given raw 12-bit values. 0x800 is the centre
    (2048 in decimal)."""
    buf = bytearray(0x14)
    buf[13:16] = _pack_stick(x_raw, y_raw)
    return bytes(buf)


def _left_packet(x_raw: int = 0x800, y_raw: int = 0x800) -> bytes:
    """Build a 0x14-byte packet with the left-side stick (offset
    10..12) set to the given raw 12-bit values."""
    buf = bytearray(0x14)
    buf[10:13] = _pack_stick(x_raw, y_raw)
    return bytes(buf)


class TestStickCenterIsNoOp:
    """A stick at centre (raw 2048) is inside the 0.1 deadzone — the
    parser must NOT touch the accumulators."""

    def test_right_stick_centered_leaves_accums_unchanged(self):
        import parser.sticks
        state = _state("right")
        state._scroll_x_accum = 42.0
        state._scroll_y_accum = 17.0
        parser.sticks.parse(state, _right_packet(0x800, 0x800))
        assert state._scroll_x_accum == 42.0
        assert state._scroll_y_accum == 17.0


class TestStickDeflectionAccumulates:
    """A strong stick deflection must push the accumulator in the
    correct direction. The sign convention in ``parser/sticks.py`` is
    ``sx = -x * ...`` (negative-x to invert scroll horizontally) and
    ``sy = +y * ...``, so a strongly POSITIVE raw x_raw (deflected
    right) should produce a NEGATIVE ``_scroll_x_accum`` delta."""

    def test_strong_positive_x_deflection_makes_scroll_x_negative(self, monkeypatch):
        """x_raw = 0xFFF (full positive deflection) with default profile
        and sensitivity → _scroll_x_accum becomes non-zero and negative
        (matching ``sx = -x * 80.0`` in the dynamic profile branch)."""
        import parser.sticks
        # Force the gaming profile so the math is linear (sx = -x*60*mult)
        # and we don't depend on cubic curve details.
        monkeypatch.setitem(parser.sticks.settings, "profile", "gaming")
        monkeypatch.setitem(parser.sticks.settings, "disable_acceleration", True)
        monkeypatch.setitem(parser.sticks.settings, "scroll_sensitivity", 4)

        state = _state("right")
        parser.sticks.parse(state, _right_packet(0xFFF, 0x800))
        # x = (4095 - 2048) / 2048 ≈ +0.9995
        # sx = -x * 60 * 1.0 ≈ -59.97
        assert state._scroll_x_accum < 0
        assert state._scroll_x_accum < -30   # well above the 0.1 noise floor


class TestRuntPacketGuard:
    """Audit finding I2 — ``parser/sticks.py`` declares ``if len(data)
    < 16: return`` at the top so a runt packet can't carry either
    side's stick block. The bounds check must hold for both sides AND
    must not crash on extremely short input.
    """

    def test_short_packet_returns_early_without_crashing(self):
        """A 15-byte packet (one short of the 16 required) must early-
        return without touching accumulators or raising."""
        import parser.sticks
        state = _state("right")
        state._scroll_x_accum = 7.0
        state._scroll_y_accum = 3.0
        parser.sticks.parse(state, b"\x00" * 15)
        assert state._scroll_x_accum == 7.0
        assert state._scroll_y_accum == 3.0

    def test_empty_packet_does_not_crash(self):
        """Empty bytes — the bounds guard at the top must catch this
        before any indexing happens."""
        import parser.sticks
        state = _state("right")
        parser.sticks.parse(state, b"")
        # Should reach here without exception.
        assert state._scroll_x_accum == 0.0


class TestLeftSideOffset:
    """Left Joy-Con reads stick bytes at offset 10..12, NOT 13..15.

    Side-offset selection in ``parser/sticks.py:24`` is the obvious
    regression point — swapping the ternary silently reads the wrong
    side's bytes (which on a left-only packet would be zero,
    suppressing the scroll output).
    """

    def test_left_side_reads_offset_10_to_13(self, monkeypatch):
        """A left-side packet with strong x deflection at offset 10..12
        must drive the scroll accumulator. If the parser reads
        offset 13..15 by mistake, those bytes are zero (= 0x000 raw,
        which is full-negative-x, opposite sign) — both behaviors are
        wrong, but only the correct one produces a deflection
        consistent with ``decode_joystick(_pack_stick(0xFFF, 0x800))``
        being strongly positive-x and ``sx`` being correspondingly
        strongly negative.

        We assert the LEFT bytes are what got read by setting them
        and zeroing the right-side block.
        """
        import parser.sticks
        monkeypatch.setitem(parser.sticks.settings, "profile", "gaming")
        monkeypatch.setitem(parser.sticks.settings, "disable_acceleration", True)
        monkeypatch.setitem(parser.sticks.settings, "scroll_sensitivity", 4)

        state = _state("left")
        # Left stick at full +x deflection; right-side bytes (13..15)
        # remain zero. If the parser reads the wrong offset it sees
        # raw 0x000 which is full-NEGATIVE x (opposite sign of what we
        # set), so the accumulator's sign distinguishes correct
        # vs. wrong offset.
        parser.sticks.parse(state, _left_packet(0xFFF, 0x800))
        assert state._scroll_x_accum < 0, (
            "left-side full-positive x deflection should produce "
            "negative _scroll_x_accum (sx = -x * 60); if zero/positive, "
            "the parser read the wrong-side offset"
        )
