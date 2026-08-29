# SPDX-License-Identifier: GPL-3.0-or-later
"""The background asyncio loop thread must run at USER_INTERACTIVE QoS on
macOS. That thread hosts every BLE notification callback and the motion
pump; on Apple Silicon the QoS class is what keeps it on a performance
core instead of an efficiency core. ``os.nice(-10)`` (the previous
approach) always fails without root and never influenced core placement.

Skipped off macOS — ``qos_class_self`` is a Darwin libc call.
"""
import asyncio
import ctypes
import ctypes.util
import sys

import pytest

import bg_loop

QOS_CLASS_USER_INTERACTIVE = 0x21


@pytest.mark.skipif(sys.platform != "darwin", reason="QoS classes are Darwin-only")
def test_background_loop_thread_runs_user_interactive():
    libc = ctypes.CDLL(ctypes.util.find_library("c"))
    libc.qos_class_self.restype = ctypes.c_uint

    async def _probe():
        return libc.qos_class_self()

    assert bg_loop.run(_probe()).result(timeout=5) == QOS_CLASS_USER_INTERACTIVE


def test_boost_thread_qos_is_noop_off_darwin(monkeypatch):
    from osio import boost
    monkeypatch.setattr(boost.sys, "platform", "linux")
    assert boost.boost_current_thread_qos() is False


_ = asyncio
