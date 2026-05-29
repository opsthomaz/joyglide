# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared helpers — stick decoder + asset path resolver.

Following the modular blueprint refactor, this module is intentionally
small. Domain-specific constants live with their consumers:
  * Joy-Con 2 BLE protocol constants → ``ble.constants`` + ``ble.feature_flags``
  * Input-report byte offsets       → ``parser.constants``
  * Pump tuning constants           → ``engine.tuning``
"""
import sys
from os import path


def unpack_stick_12bit(b0: int, b1: int, b2: int) -> tuple[int, int]:
    """Unpack the Joy-Con's packed-12-bit-per-axis stick format from its 3
    wire bytes into raw ``(x, y)`` values in 0..4095.

        x = ((b1 & 0x0F) << 8) | b0
        y = (b2 << 4) | ((b1 & 0xF0) >> 4)

    This is the protocol-load-bearing bit math; ``decode_joystick`` (here)
    and ``parser.sticks`` both build on it so a future offset/packing fix
    lives in exactly one place.
    """
    x_raw = ((b1 & 0x0F) << 8) | b0
    y_raw = (b2 << 4) | ((b1 & 0xF0) >> 4)
    return x_raw, y_raw


def decode_joystick(data: bytes) -> tuple[int, int]:
    """Decode a 3-byte packed-12-bit stick reading into normalised int16.

    Joy-Con sticks are 12-bit-per-axis values packed into 3 bytes (see
    ``unpack_stick_12bit``). Result is centred around 2048 (raw 0..4095) and
    normalised to [-1, 1], then scaled to int16 range so consumers can treat
    it like an Xbox stick.
    """
    try:
        if len(data) != 3:
            return 0, 0
        x_raw, y_raw = unpack_stick_12bit(data[0], data[1], data[2])
        x = (x_raw - 2048) / 2048.0
        y = (y_raw - 2048) / 2048.0
        deadzone = 0.08
        if abs(x) < deadzone and abs(y) < deadzone:
            return 0, 0
        # Scale by 1.7 so the user reaches full deflection before the
        # physical max — matches the feel of the real Switch.
        x = max(-1.0, min(1.0, x * 1.7))
        y = max(-1.0, min(1.0, y * 1.7))
        return int(x * 32767), int(y * 32767)
    except Exception:
        return 0, 0


def resource_path(relative_path: str) -> str:
    """Resolve a path bundled with the app, on dev or any frozen build.

    PyInstaller (both macOS .app and Windows .exe) extracts bundled data
    into a temp dir and exposes it as ``sys._MEIPASS``. Without this,
    ``assets/joyglide.png`` and friends are looked up relative to cwd —
    which is ``/`` when launching a .app from Finder, causing FileNotFoundError.

    Falls back to the source directory when running from source.
    """
    base_path = getattr(sys, "_MEIPASS", path.dirname(path.abspath(__file__)))
    return path.join(base_path, relative_path)
