# SPDX-License-Identifier: GPL-3.0-or-later
"""Single-controller BLE notification dispatcher.

This module is the bridge between bleak's notification callback and the
``JoyCon`` motion engine. It runs synchronously (no ``async`` — see notes)
and fans the raw input report bytes out to the engine's three parsers.

Why sync, not async:
    The original implementation made this an ``async def``, but bleak
    happily accepts sync callbacks too, and the work here is pure CPU
    (byte parsing, no I/O). Going sync removes a per-packet coroutine
    allocation; at 33-67 Hz that's a small but constant win in the
    hottest path of the entire application.
"""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from joycon import JoyCon


def handle_single_notification(sender, data: bytes, gamepad: "JoyCon | None") -> None:
    """Dispatch one BLE input report 0x05 packet into the motion engine.

    Order matters:

    1. ``track_packet_rate`` — runs on EVERY packet, so the pump's drain
       factor adapts even when the JC isn't on a surface (otherwise the EMA
       would stall when the user lifts the controller).
    2. ``process_battery`` — cheap, throttled internally to 1 Hz; runs early
       so the UI sees updates even on packets with no sensor data.
    3. ``process_mouse`` — the latency-critical path.
    4. ``process_buttons`` / ``process_sticks`` — order between them is
       irrelevant.
    """
    if gamepad:
        gamepad.track_packet_rate()
        gamepad.process_battery(data)
        gamepad.process_mouse(data)
        gamepad.process_buttons(data)
        gamepad.process_sticks(data)
        gamepad.process_imu(data)
        gamepad.process_magnetometer(data)


def handle_side_specific_notification(sender, data: bytes, gamepad: "JoyCon | None") -> None:
    """Dispatch one side-specific input report (0x07 JC-L / 0x08 JC-R) packet.

    We subscribe to this report SOLELY to read the Power Info bitfield
    at offset 0x1 — the JC2 firmware's own battery-level estimate.
    Other fields in this report (relative mouse, multi-sample motion,
    side-specific buttons) are either redundant with input report 0x05
    or in unknown packed formats per ndeadly, so we ignore them.
    """
    if gamepad:
        gamepad.process_power_info(data)
