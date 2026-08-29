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
