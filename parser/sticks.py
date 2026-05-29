# SPDX-License-Identifier: GPL-3.0-or-later
"""Analog stick parser — reads packed-12-bit stick values, applies a
profile-aware curve, accumulates into the engine's scroll bucket.
"""
import time

from parser.constants import STICK_BLOCK_LEN, STICK_LEFT_OFFSET, STICK_RIGHT_OFFSET
from user_preferences import settings
from utils import unpack_stick_12bit


def parse(state, data: bytes) -> None:
    """Read the active-side stick, push scroll deltas into the accumulator."""
    # Same paused-gate rationale as parser.mouse_optical: avoid building up
    # _scroll_*_accum during pause that would burst on resume.
    if state.paused:
        return

    # Runt-packet guard at the top — the right-side stick block ends at
    # STICK_RIGHT_OFFSET + STICK_BLOCK_LEN, so a shorter packet can't carry
    # either side's stick. The previous ``len(stick_data) != 3`` check only
    # caught the left-side runt case after slicing.
    if len(data) < STICK_RIGHT_OFFSET + STICK_BLOCK_LEN:
        return

    off = STICK_LEFT_OFFSET if state.is_left else STICK_RIGHT_OFFSET
    x_raw, y_raw = unpack_stick_12bit(data[off], data[off + 1], data[off + 2])
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
