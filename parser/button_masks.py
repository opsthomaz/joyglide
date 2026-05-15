# SPDX-License-Identifier: GPL-3.0-or-later
"""Joy-Con 2 button bitmask layout for input report 0x05.

Each Joy-Con side has its own 24-bit field starting at a different offset:
  * Right Joy-Con: bytes 0x03..0x05 (big-endian)
  * Left Joy-Con:  bytes 0x04..0x06 (big-endian)

Names match the physical Joy-Con labels (PLUS/MINUS, ZL/ZR, SL/SR,
HOME, CHAT, SHARE).

(Note: per ndeadly's hid_reports.md the canonical 32-bit button field
in input report 0x05 spans bytes 0x4..0x7 with explicit per-byte
positions. Our implementation reads 3 bytes at side-specific offsets;
this works because the side-specific bits don't overlap. If a future
contributor wants to adopt ndeadly's layout for clearer parsing,
``german77/JoyconDriver/switch2/input_handler.lua:parse_buttons2`` is
the reference.)
"""

MASKS = {
    "right": {
        "A":    0x000800,
        "B":    0x000400,
        "X":    0x000200,
        "Y":    0x000100,
        "PLUS": 0x000002,
        "STICK":0x000004,
        "SL":   0x002000,
        "SR":   0x001000,
        "R":    0x004000,
        "ZR":   0x008000,
        "HOME": 0x000010,
        "CHAT": 0x000040,
    },
    "left": {
        "UP":    0x000002,
        "DOWN":  0x000001,
        "LEFT":  0x000008,
        "RIGHT": 0x000004,
        "MINUS": 0x000100,
        "STICK": 0x000800,
        "SHARE": 0x002000,
        "SL":    0x000020,
        "SR":    0x000010,
        "L":     0x000040,
        "ZL":    0x000080,
    }
}
