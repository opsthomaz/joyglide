# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for ``osio.boost`` — the macOS anti-throttle / priority hooks.

``_boost_macos`` talks to ``Foundation.NSProcessInfo``; on non-Darwin CI
that module doesn't exist, so the tests inject a fake ``Foundation``
module into ``sys.modules`` carrying the real numeric values of the
``NSActivity*`` constants (verified against pyobjc 12 on macOS 26):

    NSActivityIdleSystemSleepDisabled              = 0x0000000000100000
    NSActivityUserInitiatedAllowingIdleSystemSleep = 0x0000000000EFFFFF
    NSActivityUserInitiated                        = 0x0000000000FFFFFF
    NSActivityLatencyCritical                      = 0x000000FF00000000

Regression pinned here: the original code passed the literal
``0x00FFFFFF`` believing it was *AllowingIdleSystemSleep*; it is actually
``NSActivityUserInitiated``, which includes ``IdleSystemSleepDisabled`` —
so the app silently kept the Mac from idle-sleeping while running.
"""
import os
import sys
import types

import pytest

import osio.boost as boost

_IDLE_SLEEP_DISABLED = 0x0000000000100000
_USER_INITIATED_ALLOWING_IDLE_SLEEP = 0x0000000000EFFFFF
_LATENCY_CRITICAL = 0x000000FF00000000


class _FakeProcessInfo:
    """Records the options/reason handed to ``beginActivityWithOptions_reason_``."""

    calls: list[tuple[int, str]] = []

    @classmethod
    def processInfo(cls):
        return cls()

    def beginActivityWithOptions_reason_(self, options, reason):
        _FakeProcessInfo.calls.append((options, reason))
        return object()


@pytest.fixture
def fake_foundation(monkeypatch):
    """Inject a stand-in ``Foundation`` module and silence ``os.nice``."""
    _FakeProcessInfo.calls = []
    mod = types.ModuleType("Foundation")
    mod.NSProcessInfo = _FakeProcessInfo
    mod.NSActivityIdleSystemSleepDisabled = _IDLE_SLEEP_DISABLED
    mod.NSActivityUserInitiatedAllowingIdleSystemSleep = _USER_INITIATED_ALLOWING_IDLE_SLEEP
    mod.NSActivityLatencyCritical = _LATENCY_CRITICAL
    monkeypatch.setitem(sys.modules, "Foundation", mod)
    # os.nice(-10) needs root; never let the test renice pytest itself.
    def _deny(_n):
        raise PermissionError
    monkeypatch.setattr(os, "nice", _deny)
    monkeypatch.setattr(boost, "_ANTI_NAP_ACTIVITY", None)
    return mod


class TestAntiAppNapOptions:
    def test_does_not_disable_idle_system_sleep(self, fake_foundation):
        """The activity must allow idle system sleep — an input helper has
        no business keeping the whole Mac awake."""
        boost._boost_macos()
        (options, _reason), = _FakeProcessInfo.calls
        assert options & _IDLE_SLEEP_DISABLED == 0

    def test_requests_latency_critical_timers(self, fake_foundation):
        """``NSActivityLatencyCritical`` is Apple's documented flag for
        work that needs the highest timer/I-O availability; without it the
        pump's ~16 ms ``asyncio.sleep`` deadlines are subject to timer
        coalescing whenever the window is unfocused."""
        boost._boost_macos()
        (options, _reason), = _FakeProcessInfo.calls
        assert options & _LATENCY_CRITICAL == _LATENCY_CRITICAL

    def test_keeps_user_initiated_allowing_idle_sleep_bits(self, fake_foundation):
        boost._boost_macos()
        (options, _reason), = _FakeProcessInfo.calls
        assert options & _USER_INITIATED_ALLOWING_IDLE_SLEEP == _USER_INITIATED_ALLOWING_IDLE_SLEEP

    def test_activity_token_is_retained(self, fake_foundation):
        """The NSActivity token must be held for the process lifetime —
        dropping it ends the activity immediately."""
        boost._boost_macos()
        assert boost._ANTI_NAP_ACTIVITY is not None
