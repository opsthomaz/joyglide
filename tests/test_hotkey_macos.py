# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for ``osio.hotkey.macos`` — the ⌃⌥M CGEventTap callback.

Skipped off macOS (``Quartz`` import). The tap itself is never created;
we build the callback with ``_make_tap_callback`` and feed it synthetic
event types, monkey-patching the Quartz field getters.

Regression pinned: WindowServer disables an event tap whose callback is
too slow (``kCGEventTapDisabledByTimeout``) or on some user-input
transitions (``kCGEventTapDisabledByUserInput``). It tells the callback
by invoking it with that pseudo event type — and unless the callback
re-enables the tap, the hotkey is dead until the app restarts. A Python
callback stalled by the GIL during a BLE burst is exactly the trigger.
"""
import pytest

pytest.importorskip("Quartz", reason="Quartz is macOS-only")

import osio.hotkey.macos as hk


@pytest.fixture
def quartz(monkeypatch):
    """Stub the Quartz calls the callback makes; return a mutable state dict."""
    state = {"autorepeat": 0, "keycode": hk._KEY_M, "flags": 0, "enabled": []}
    monkeypatch.setattr(hk, "CGEventGetIntegerValueField",
                        lambda _ev, field: state["autorepeat"]
                        if field == hk.kCGKeyboardEventAutorepeat else state["keycode"])
    monkeypatch.setattr(hk, "CGEventGetFlags", lambda _ev: state["flags"])
    monkeypatch.setattr(hk, "CGEventTapEnable",
                        lambda tap, on: state["enabled"].append((tap, on)))
    return state


class TestTapReenable:
    def test_disabled_by_timeout_reenables_tap(self, quartz):
        tap = object()
        fired = []
        cb = hk._make_tap_callback(fired.append, tap_holder=[tap])
        cb(None, hk.kCGEventTapDisabledByTimeout, "ev", None)
        assert quartz["enabled"] == [(tap, True)]
        assert fired == []

    def test_disabled_by_user_input_reenables_tap(self, quartz):
        tap = object()
        cb = hk._make_tap_callback(lambda: None, tap_holder=[tap])
        cb(None, hk.kCGEventTapDisabledByUserInput, "ev", None)
        assert quartz["enabled"] == [(tap, True)]


class TestHotkeyMatch:
    def test_ctrl_opt_m_fires_callback(self, quartz):
        quartz["flags"] = hk.kCGEventFlagMaskControl | hk.kCGEventFlagMaskAlternate
        fired = []
        cb = hk._make_tap_callback(lambda: fired.append(1), tap_holder=[object()])
        cb(None, hk.kCGEventKeyDown, "ev", None)
        assert fired == [1]

    def test_autorepeat_is_ignored(self, quartz):
        quartz["flags"] = hk.kCGEventFlagMaskControl | hk.kCGEventFlagMaskAlternate
        quartz["autorepeat"] = 1
        fired = []
        cb = hk._make_tap_callback(lambda: fired.append(1), tap_holder=[object()])
        cb(None, hk.kCGEventKeyDown, "ev", None)
        assert fired == []

    def test_extra_modifier_does_not_fire(self, quartz):
        quartz["flags"] = (hk.kCGEventFlagMaskControl | hk.kCGEventFlagMaskAlternate
                           | hk.kCGEventFlagMaskShift)
        fired = []
        cb = hk._make_tap_callback(lambda: fired.append(1), tap_holder=[object()])
        cb(None, hk.kCGEventKeyDown, "ev", None)
        assert fired == []
