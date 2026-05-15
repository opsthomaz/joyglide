# SPDX-License-Identifier: GPL-3.0-or-later
"""Analog stick parser — reads packed-12-bit stick values, applies a
profile-aware curve, accumulates into the engine's scroll bucket.
"""
import time

from user_preferences import settings


def parse(state, data: bytes) -> None:
    """Read the active-side stick, push scroll deltas into the accumulator."""
    # Same paused-gate rationale as parser.mouse_optical: avoid building up
    # _scroll_*_accum during pause that would burst on resume.
    if state.paused:
        return

    stick_data = data[10:13] if state.is_left else data[13:16]
    if len(stick_data) != 3:
        return

    x_raw = ((stick_data[1] & 0x0F) << 8) | stick_data[0]
    y_raw = (stick_data[2] << 4) | ((stick_data[1] & 0xF0) >> 4)
    x = (x_raw - 2048) / 2048.0
    y = (y_raw - 2048) / 2048.0

    deadzone = 0.1
    if abs(x) < deadzone and abs(y) < deadzone:
        return

    profile = settings.get("profile", "dynamic")
    disable_accel = settings.get("disable_acceleration", True)

    scroll_mult = settings.get("scroll_sensitivity", 4) / 4.0

    if disable_accel or profile == "gaming":
        sx = -x * 60.0 * scroll_mult
        sy =  y * 60.0 * scroll_mult
    elif profile == "cinematic":
        sx = -(x ** 3) * 35.0 * scroll_mult
        sy =  (y ** 3) * 35.0 * scroll_mult
    else: # Dynamic
        sx = -(x ** 3) * 80.0 * scroll_mult
        sy =  (y ** 3) * 80.0 * scroll_mult

    if abs(sx) > 0.1 or abs(sy) > 0.1:
        state._scroll_x_accum += sx
        state._scroll_y_accum += sy
        state._last_motion_ts = time.monotonic()
        state.start_pump()
