# SPDX-License-Identifier: GPL-3.0-or-later
"""Direct unit tests for ``parser.buttons``.

The button parser reads a side-specific 24-bit field from input report
0x05 (offset 3..5 for the right Joy-Con, 4..6 for the left), diffs it
against the previous packet, and dispatches click events on the state's
``input_simulator``. The two side offsets, the bitmask layout, and the
press→release dispatch table are all places where a regression would
silently mis-fire mouse events with no exception to flag the bug.

Cross-references:
  * ``parser/buttons.py`` — the parser under test
  * ``parser/button_masks.py`` — bitmask definitions per side
  * Audit finding I1 — bounds check on ``data[offset:offset+3]``
"""
from unittest.mock import MagicMock

import parser.buttons
from parser.button_masks import MASKS


def _state(side: str = "right"):
    """Build a minimum-viable state object for the button parser.

    The parser reads ``state.side``, ``state.is_left``, ``state.paused``,
    ``state.last_data``, and dispatches to methods on
    ``state.input_simulator``. Mirrors the attribute shape the real
    ``JoyCon`` exposes without spinning up the full engine.
    """
    s = MagicMock()
    s.side = side
    s.is_left = (side == "left")
    s.paused = False
    s.last_data = None
    s._held_ups = {}
    return s


def _right_packet(mask: int) -> bytes:
    """Build a 0x14-byte packet with the right-side 24-bit button field
    set to ``mask`` (big-endian at offset 3..5)."""
    buf = bytearray(0x14)
    buf[3] = (mask >> 16) & 0xFF
    buf[4] = (mask >> 8) & 0xFF
    buf[5] = mask & 0xFF
    return bytes(buf)


def _left_packet(mask: int) -> bytes:
    """Build a 0x14-byte packet with the left-side 24-bit button field
    set to ``mask`` (big-endian at offset 4..6)."""
    buf = bytearray(0x14)
    buf[4] = (mask >> 16) & 0xFF
    buf[5] = (mask >> 8) & 0xFF
    buf[6] = mask & 0xFF
    return bytes(buf)


class TestRightSideClicks:
    """Right Joy-Con button transitions → InputSimulator dispatch.

    The right-side offset is 3 (per ``parser/buttons.py:58``) and the
    masks come from ``MASKS["right"]`` in ``parser/button_masks.py``.
    """

    def test_R_press_fires_mouse_down(self):
        """R (0x004000) transitioning from released → pressed must
        invoke ``mouse_down`` exactly once."""
        state = _state("right")
        state.last_data = _right_packet(0x000000)   # nothing pressed
        parser.buttons.parse(state, _right_packet(MASKS["right"]["R"]))
        state.input_simulator.mouse_down.assert_called_once()
        state.input_simulator.mouse_up.assert_not_called()

    def test_R_release_fires_mouse_up(self):
        """R transitioning from pressed → released must invoke
        ``mouse_up`` exactly once."""
        state = _state("right")
        state.last_data = _right_packet(MASKS["right"]["R"])   # R held
        parser.buttons.parse(state, _right_packet(0x000000))
        state.input_simulator.mouse_up.assert_called_once()
        state.input_simulator.mouse_down.assert_not_called()

    def test_ZR_press_fires_mouse_down_right(self):
        """ZR (0x008000) maps to the right (context) mouse button."""
        state = _state("right")
        state.last_data = _right_packet(0x000000)
        parser.buttons.parse(state, _right_packet(MASKS["right"]["ZR"]))
        state.input_simulator.mouse_down_right.assert_called_once()

    def test_STICK_press_fires_mouse_down_middle(self):
        """Pressing the analog stick → middle-mouse-button down."""
        state = _state("right")
        state.last_data = _right_packet(0x000000)
        parser.buttons.parse(state, _right_packet(MASKS["right"]["STICK"]))
        state.input_simulator.mouse_down_middle.assert_called_once()


class TestLeftSideOffset:
    """Left Joy-Con uses a DIFFERENT byte offset (4..6, not 3..5).

    The two side offsets are the easiest place to regress because
    a one-character change (``4 if state.is_left else 3``) reverses
    the L/R mapping silently.
    """

    def test_left_L_press_uses_offset_4(self):
        """The left-side L button (mask 0x000040 at offset 4..6) must
        fire ``mouse_down``. If the parser reads offset 3 by mistake,
        the byte at position 3 is zero and no event would fire."""
        state = _state("left")
        state.last_data = _left_packet(0x000000)
        parser.buttons.parse(state, _left_packet(MASKS["left"]["L"]))
        state.input_simulator.mouse_down.assert_called_once()


class TestRuntPacketGuard:
    """Audit finding I1 — ``data[offset:offset+3]`` without a bounds
    check would silently return a short slice and ``int.from_bytes``
    would decode it as a smaller magnitude, producing a spurious
    bitmask diff that synthesises phantom press/release events.

    The parser must early-return on packets shorter than ``offset + 3``.
    """

    def test_short_packet_returns_early_without_firing(self):
        """A 4-byte packet can't carry the right-side 24-bit field
        (offset 3..5 needs 6 bytes minimum). Parser must NOT crash AND
        must NOT fire any click events."""
        state = _state("right")
        state.last_data = _right_packet(0x000000)
        # 4 bytes — strictly less than offset(3) + 3 = 6
        parser.buttons.parse(state, b"\x00\x00\x00\x00")
        # No dispatch methods called at all.
        state.input_simulator.mouse_down.assert_not_called()
        state.input_simulator.mouse_up.assert_not_called()
        state.input_simulator.mouse_down_right.assert_not_called()
        state.input_simulator.mouse_up_right.assert_not_called()
        state.input_simulator.mouse_down_middle.assert_not_called()

    def test_short_packet_does_not_overwrite_last_data(self):
        """A runt packet must NOT advance ``last_data``, otherwise the
        next full packet would diff against a truncated state and emit
        ghost events."""
        state = _state("right")
        prev = _right_packet(0x000000)
        state.last_data = prev
        parser.buttons.parse(state, b"\x00\x00\x00\x00")
        assert state.last_data == prev


class TestSwapClickButtons:
    """``settings["swap_click_buttons"]`` flips the click layout: trigger
    (ZL/ZR) becomes the left click and shoulder (L/R) the right click.
    Default off keeps the documented layout (shoulder = left)."""

    def test_default_layout_shoulder_is_left(self, monkeypatch):
        monkeypatch.setitem(parser.buttons.settings, "swap_click_buttons", False)
        state = _state("right")
        parser.buttons.parse(state, _right_packet(0))
        parser.buttons.parse(state, _right_packet(MASKS["right"]["R"]))
        state.input_simulator.mouse_down.assert_called_once()
        state.input_simulator.mouse_down_right.assert_not_called()

    def test_swapped_R_fires_right_click(self, monkeypatch):
        monkeypatch.setitem(parser.buttons.settings, "swap_click_buttons", True)
        state = _state("right")
        parser.buttons.parse(state, _right_packet(0))
        parser.buttons.parse(state, _right_packet(MASKS["right"]["R"]))
        state.input_simulator.mouse_down_right.assert_called_once()
        state.input_simulator.mouse_down.assert_not_called()

    def test_swapped_ZR_fires_left_click(self, monkeypatch):
        monkeypatch.setitem(parser.buttons.settings, "swap_click_buttons", True)
        state = _state("right")
        parser.buttons.parse(state, _right_packet(0))
        parser.buttons.parse(state, _right_packet(MASKS["right"]["ZR"]))
        state.input_simulator.mouse_down.assert_called_once()
        state.input_simulator.mouse_down_right.assert_not_called()

    def test_swapped_left_side_ZL_fires_left_click(self, monkeypatch):
        monkeypatch.setitem(parser.buttons.settings, "swap_click_buttons", True)
        state = _state("left")
        parser.buttons.parse(state, _left_packet(0))
        parser.buttons.parse(state, _left_packet(MASKS["left"]["ZL"]))
        state.input_simulator.mouse_down.assert_called_once()

    def test_release_uses_layout_active_at_press(self, monkeypatch):
        """Toggling the swap while a button is held must not strand a
        button: the release fires the counterpart of whatever the press
        fired, not whatever the new layout says."""
        monkeypatch.setitem(parser.buttons.settings, "swap_click_buttons", False)
        state = _state("right")
        parser.buttons.parse(state, _right_packet(0))
        parser.buttons.parse(state, _right_packet(MASKS["right"]["R"]))   # left down
        monkeypatch.setitem(parser.buttons.settings, "swap_click_buttons", True)
        parser.buttons.parse(state, _right_packet(0))                     # release
        state.input_simulator.mouse_up.assert_called_once()
        state.input_simulator.mouse_up_right.assert_not_called()


class TestButtonMap:
    """``settings["button_map"]`` (button name → action) is the source of
    truth for what each button does; ``swap_click_buttons`` flips
    left/right on top of it."""

    def test_default_map_matches_documented_layout(self):
        from user_preferences import DEFAULT_BUTTON_MAP
        assert DEFAULT_BUTTON_MAP == {
            "L": "left", "R": "left", "ZL": "right", "ZR": "right",
            "STICK": "middle", "A": "forward", "Y": "back",
            "LEFT": "back", "RIGHT": "forward",
        }

    def test_remapped_A_fires_middle_click(self, monkeypatch):
        from user_preferences import DEFAULT_BUTTON_MAP
        monkeypatch.setitem(parser.buttons.settings, "swap_click_buttons", False)
        monkeypatch.setitem(parser.buttons.settings, "button_map", {**DEFAULT_BUTTON_MAP, "A": "middle"})
        state = _state("right")
        parser.buttons.parse(state, _right_packet(0))
        parser.buttons.parse(state, _right_packet(MASKS["right"]["A"]))
        state.input_simulator.mouse_down_middle.assert_called_once()
        state.input_simulator.mouse_down_forward.assert_not_called()

    def test_action_none_fires_nothing(self, monkeypatch):
        from user_preferences import DEFAULT_BUTTON_MAP
        monkeypatch.setitem(parser.buttons.settings, "swap_click_buttons", False)
        monkeypatch.setitem(parser.buttons.settings, "button_map", {**DEFAULT_BUTTON_MAP, "R": "none"})
        state = _state("right")
        parser.buttons.parse(state, _right_packet(0))
        parser.buttons.parse(state, _right_packet(MASKS["right"]["R"]))
        parser.buttons.parse(state, _right_packet(0))
        state.input_simulator.mouse_down.assert_not_called()
        state.input_simulator.mouse_up.assert_not_called()

    def test_swap_applies_on_top_of_map(self, monkeypatch):
        """R remapped to "right" + swap on → left click."""
        from user_preferences import DEFAULT_BUTTON_MAP
        monkeypatch.setitem(parser.buttons.settings, "swap_click_buttons", True)
        monkeypatch.setitem(parser.buttons.settings, "button_map", {**DEFAULT_BUTTON_MAP, "R": "right"})
        state = _state("right")
        parser.buttons.parse(state, _right_packet(0))
        parser.buttons.parse(state, _right_packet(MASKS["right"]["R"]))
        state.input_simulator.mouse_down.assert_called_once()
        state.input_simulator.mouse_down_right.assert_not_called()
