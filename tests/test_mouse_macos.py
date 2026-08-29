# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for ``osio.mouse.macos`` — Quartz event construction.

Skipped wholesale off macOS (``Quartz`` isn't importable there). Nothing
here posts an event: ``CGEventPost`` is monkey-patched to capture the
built ``CGEvent`` so its fields can be inspected with the real
``CGEventGetIntegerValueField``.

Regression pinned: ``CGEventCreateMouseEvent`` leaves
``kCGMouseEventDeltaX/Y`` at 0 (verified on macOS 26). Apps that read raw
deltas instead of absolute position — FPS games with a captured cursor,
``NSEvent.deltaX`` consumers — saw no motion at all from Joyglide, which is
exactly the audience of the Gaming profile.
"""
import pytest

CG = pytest.importorskip("Quartz.CoreGraphics", reason="Quartz is macOS-only")

import osio.mouse.macos as m  # noqa: E402


@pytest.fixture
def posted(monkeypatch):
    """Capture every CGEvent handed to ``CGEventPost`` instead of posting."""
    events = []
    monkeypatch.setattr(m.CG, "CGEventPost", lambda _tap, ev: events.append(ev))
    return events


def _delta(ev):
    return (CG.CGEventGetIntegerValueField(ev, CG.kCGMouseEventDeltaX),
            CG.CGEventGetIntegerValueField(ev, CG.kCGMouseEventDeltaY))


class TestMoveEventsCarryDeltas:
    def test_mouse_moved_sets_delta_fields(self, posted):
        m._post(CG.kCGEventMouseMoved, (100.0, 100.0), delta=(3.0, -2.0))
        assert _delta(posted[0]) == (3, -2)

    def test_fractional_delta_rounds_to_nearest_integer(self, posted):
        """The delta fields are integer-valued; round (don't truncate) so a
        0.6 px move isn't reported as zero motion."""
        m._post(CG.kCGEventMouseMoved, (100.0, 100.0), delta=(0.6, -1.4))
        assert _delta(posted[0]) == (1, -1)

    def test_drag_event_sets_delta_fields(self, posted):
        m._post(CG.kCGEventLeftMouseDragged, (10.0, 10.0), delta=(5.0, 7.0))
        assert _delta(posted[0]) == (5, 7)

    def test_click_events_do_not_touch_delta_fields(self, posted):
        m._post(CG.kCGEventLeftMouseDown, (10.0, 10.0))
        assert _delta(posted[0]) == (0, 0)


class TestInputSimulatorMoveDeltas:
    def test_mouse_move_forwards_delta_to_post(self, monkeypatch):
        calls = []
        monkeypatch.setattr(m, "_post", lambda *a, **kw: calls.append((a, kw)))
        sim = m.InputSimulator()
        sim._cx, sim._cy = 200.0, 200.0
        sim.mouse_move(4.0, -3.0)
        (_args, kwargs), = calls
        assert kwargs["delta"] == (4.0, -3.0)


class TestClampStaysInsideRealCursorBounds:
    """macOS bounds a *real* pointer to ``[0, size − 1/64]`` per axis
    (``CGWarpMouseCursorPosition(300, 99999)`` → y = 1111.984375 on a
    1112-pt display, measured 2026-08-29 on macOS 26.6) but accepts
    posted HID events at any coordinate. Clamping to ``size`` exactly
    parked our cursor 1 pt outside the valid range, where the Dock's
    edge trigger fires but its "cursor inside me?" hit-test fails —
    the auto-hidden Dock flickered up/down while the user pushed the
    Joy-Con against the bottom edge.
    """

    def _sim(self):
        sim = m.InputSimulator.__new__(m.InputSimulator)
        sim._sw, sim._sh = 1710.0, 1112.0
        return sim

    def test_bottom_edge_clamps_to_height_minus_one_64th(self):
        sim = self._sim(); sim._cx, sim._cy = 300.0, 5000.0
        sim._clamp()
        assert sim._cy == pytest.approx(1112.0 - 1 / 64)

    def test_right_edge_clamps_to_width_minus_one_64th(self):
        sim = self._sim(); sim._cx, sim._cy = 5000.0, 300.0
        sim._clamp()
        assert sim._cx == pytest.approx(1710.0 - 1 / 64)

    def test_origin_clamps_to_zero(self):
        sim = self._sim(); sim._cx, sim._cy = -40.0, -40.0
        sim._clamp()
        assert (sim._cx, sim._cy) == (0.0, 0.0)

    def test_interior_position_untouched(self):
        sim = self._sim(); sim._cx, sim._cy = 123.4, 567.8
        sim._clamp()
        assert (sim._cx, sim._cy) == (123.4, 567.8)
