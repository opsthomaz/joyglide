# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for pure helpers in ``utils.py``."""
import os
import sys

import utils
from utils import decode_joystick, resource_path


class TestDecodeJoystick:
    """``decode_joystick`` parses a 3-byte packed-12-bit stick reading and
    returns a normalised int16 ``(x, y)`` pair, applying a deadzone."""

    @staticmethod
    def encode_packed_12bit(x: int, y: int) -> bytes:
        """Inverse of decode_joystick's packing: emit 3 bytes from raw 0..4095."""
        b0 = x & 0xFF
        b1 = ((x >> 8) & 0x0F) | ((y & 0x0F) << 4)
        b2 = (y >> 4) & 0xFF
        return bytes([b0, b1, b2])

    def test_centred_stick_inside_deadzone_returns_zero(self):
        # 2048 = exact centre. The deadzone (0.08) should swallow it.
        data = self.encode_packed_12bit(2048, 2048)
        assert decode_joystick(data) == (0, 0)

    def test_full_right_returns_positive_x_max(self):
        # 4095 = max raw → +1.0 → scaled to int16 max
        data = self.encode_packed_12bit(4095, 2048)
        x, y = decode_joystick(data)
        assert x > 30000     # near int16 max (32767)
        assert y == 0        # within deadzone on Y

    def test_full_left_returns_negative_x_max(self):
        data = self.encode_packed_12bit(0, 2048)
        x, y = decode_joystick(data)
        assert x < -30000
        assert y == 0

    def test_full_up_returns_positive_y(self):
        # The Y axis convention in raw bytes — verify it produces non-zero Y
        data = self.encode_packed_12bit(2048, 4095)
        x, y = decode_joystick(data)
        assert x == 0
        assert abs(y) > 30000

    def test_corrupt_data_short_buffer_returns_zero(self):
        # Function returns (0, 0) for any buffer that isn't exactly 3 bytes.
        assert decode_joystick(b"") == (0, 0)
        assert decode_joystick(b"\x00") == (0, 0)
        assert decode_joystick(b"\x00\x00") == (0, 0)
        assert decode_joystick(b"\x00\x00\x00\x00") == (0, 0)

    def test_corrupt_data_random_garbage_clamps(self):
        # Garbage in → result must still be in valid int16 range.
        for raw in [b"\xff\xff\xff", b"\x55\xaa\x55", b"\xab\xcd\xef"]:
            x, y = decode_joystick(raw)
            assert -32768 <= x <= 32767
            assert -32768 <= y <= 32767


class TestResourcePath:
    """``resource_path`` resolves bundled assets correctly in both source-tree
    and frozen (PyInstaller) runs. The frozen path is exposed via
    ``sys._MEIPASS``; otherwise we fall back to the source directory."""

    def test_returns_path_under_source_dir_when_not_frozen(self):
        # In a normal interpreter run, sys._MEIPASS doesn't exist.
        # resource_path should join relative to the dirname of utils.py.
        assert not hasattr(sys, "_MEIPASS")
        result = resource_path("assets/joyglide.png")
        assert result.endswith(os.path.join("assets", "joyglide.png"))
        # Must be an absolute path so callers can pass it to open() without
        # depending on cwd.
        assert os.path.isabs(result)

    def test_uses_meipass_when_frozen(self, monkeypatch):
        """When PyInstaller's bootloader sets sys._MEIPASS, resource_path
        must root at THAT path (the bundle's extraction dir), not at
        the source dir — otherwise frozen builds can't find their assets."""
        fake_meipass = os.path.join(os.sep, "tmp", "fake_pyinstaller_bundle")
        monkeypatch.setattr(sys, "_MEIPASS", fake_meipass, raising=False)
        result = resource_path("assets/joyglide.png")
        assert result == os.path.join(fake_meipass, "assets", "joyglide.png")

    def test_handles_nested_subpaths(self):
        # Multi-segment relative path should resolve normally.
        result = resource_path("assets/sub/deeper/file.png")
        assert result.endswith(os.path.join("assets", "sub", "deeper", "file.png"))

    def test_handles_empty_relative_path(self):
        # Empty string → returns the base dir itself (with trailing /).
        result = resource_path("")
        assert os.path.isabs(result)
        # Should equal the source dir of utils.py (path.join with "" leaves
        # the trailing separator).
        expected = os.path.dirname(os.path.abspath(utils.__file__)) + os.sep
        assert result == expected
