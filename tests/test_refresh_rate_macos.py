# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for ``osio.mouse.macos._max_refresh_rate_across_displays``.

Skipped off macOS. ``AppKit.NSScreen`` is replaced by a fake module so the
tests don't depend on the panel the suite happens to run on.

Why ``NSScreen.maximumFramesPerSecond`` (macOS 12+): it reports the
panel's true peak rate — 120 on ProMotion — even when
``CGDisplayModeGetRefreshRate`` returns 0 for adaptive-refresh built-in
displays. The previous hard-coded ProMotion model list stopped at
``Mac16,x`` and would have run the pump at 60 Hz on every newer 120 Hz
MacBook Pro. Tier B (Apple AppKit docs).
"""
import sys
import types

import pytest

pytest.importorskip("Quartz", reason="Quartz is macOS-only")

import osio.mouse.macos as m


def _fake_appkit(monkeypatch, rates):
    class _Screen:
        def __init__(self, fps):
            self._fps = fps

        def maximumFramesPerSecond(self):
            return self._fps

    class _NSScreen:
        @staticmethod
        def screens():
            return [_Screen(r) for r in rates]

    mod = types.ModuleType("AppKit")
    mod.NSScreen = _NSScreen
    monkeypatch.setitem(sys.modules, "AppKit", mod)


class TestMaxRefreshRate:
    def test_picks_fastest_display(self, monkeypatch):
        _fake_appkit(monkeypatch, [60, 120])
        assert m._max_refresh_rate_across_displays() == 120.0

    def test_single_promotion_panel(self, monkeypatch):
        _fake_appkit(monkeypatch, [120])
        assert m._max_refresh_rate_across_displays() == 120.0

    def test_no_screens_falls_back_to_60(self, monkeypatch):
        _fake_appkit(monkeypatch, [])
        assert m._max_refresh_rate_across_displays() == 60.0

    def test_zero_rate_is_ignored(self, monkeypatch):
        """A 0 (unknown) from one panel must not win over a real value."""
        _fake_appkit(monkeypatch, [0, 60])
        assert m._max_refresh_rate_across_displays() == 60.0

    def test_no_hardware_model_heuristic_remains(self):
        """The sysctl hw.model lookup is gone — the API replaces it."""
        assert not hasattr(m, "_get_fallback_hz")
        assert not hasattr(m, "_get_hardware_model")
