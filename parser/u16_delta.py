# SPDX-License-Identifier: GPL-3.0-or-later
"""Helper: signed delta of two u16 values with wraparound.

The optical sensor reports an absolute u16 position that wraps at 0xFFFF.
A naive subtraction produces huge spikes when the value rolls; this
helper normalises the result into the range [-32768, 32767].
"""


def delta_u16(curr: int, prev: int) -> int:
    """Return ``curr - prev`` as a signed-16-bit value, accounting for
    u16 wraparound. Result is always in the range [-32768, 32767]."""
    d = (curr - prev) & 0xFFFF
    return d - 0x10000 if d > 0x7FFF else d
