# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests for CLAUDE.md §3 hard invariants in ``ble/protocol.py``.

These tests pin behavior that has been regressed multiple times in this
codebase's history (see the v0.6.0 "no-padding" fix and the
``FEATURE_MASK_DEFAULT`` 0x33→0xFF revert). Failure of any of these means
the invariant is broken; do NOT silence them — fix the regression.

Cross-references:
  * CLAUDE.md §3 #1 — ``write_command`` sends EXACTLY ``len(payload)``
    bytes, never padded.
  * CLAUDE.md §3 #2 — ``FEATURE_MASK_DEFAULT`` must remain ``0xFF``.
  * ``ble/protocol.py`` docstring — header byte layout (ndeadly's
    commands.md "Command Header" table).
"""
import asyncio
from unittest.mock import AsyncMock

import pytest

import ble.protocol as proto
from ble.feature_flags import (
    FEATURE_BUTTON,
    FEATURE_IMU,
    FEATURE_MAGNETOMETER,
    FEATURE_MASK_DEFAULT,
    FEATURE_MOUSE,
    FEATURE_RUMBLE,
    FEATURE_STICK,
)


@pytest.fixture
def client() -> AsyncMock:
    """Mock ``BleakClient`` — ``write_gatt_char`` is recorded for inspection.

    ``client.write_gatt_char(uuid, data)`` → ``await_args.args[1]`` is the
    data argument written to the GATT characteristic.
    """
    c = AsyncMock()
    c.write_gatt_char = AsyncMock()
    return c


class TestWriteCommandNoPadding:
    """CLAUDE.md §3 #1 — ``write_command`` sends EXACTLY ``len(payload)``
    bytes on the wire, never padded.

    The historical bug (fixed in v0.6.0): payload was zero-padded to 8
    bytes while the length byte (header[5]) reported the original
    (shorter) length. The JC2 firmware silently rejected the mismatched
    packets — LEDs stayed in pairing-cycle, no vibration, mouse data
    zeroed. The ``coffincolors/jc2mouse`` Linux driver does NOT pad; that
    is the empirically-validated path.
    """

    def test_single_byte_payload_writes_9_bytes_total(self, client):
        """1-byte payload → 8-byte header + 1 byte = 9 bytes on the wire.
        Length byte must equal 1, not 8."""
        asyncio.run(proto.write_command(client, 0x09, 0x07, b"\x01"))
        written = client.write_gatt_char.await_args.args[1]
        assert written[5] == 1, "length byte must equal len(data)"
        assert len(written) == 9, "8-byte header + exactly 1 payload byte"

    def test_four_byte_payload_writes_12_bytes_total(self, client):
        """4-byte payload (mirrors the actual Feature Select payload)."""
        asyncio.run(proto.write_command(
            client, 0x0C, 0x02, bytes([0x00, 0x04, 0x00, 0x00])
        ))
        written = client.write_gatt_char.await_args.args[1]
        assert written[5] == 4
        assert len(written) == 12

    def test_empty_payload_writes_only_header(self, client):
        """Empty payload → exactly the 8-byte header, no trailing bytes
        (the ``cancel_bluetooth_advertising`` path uses this)."""
        asyncio.run(proto.write_command(client, 0x03, 0x02, b""))
        written = client.write_gatt_char.await_args.args[1]
        assert written[5] == 0
        assert len(written) == 8

    def test_eight_byte_payload_not_padded_further(self, client):
        """An already-8-byte payload (set_leds path) must not be
        re-padded to 16. Pins that the function isn't naively adding
        a fixed-size pad regardless of input length."""
        payload = bytes([0x03, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
        asyncio.run(proto.write_command(client, 0x09, 0x07, payload))
        written = client.write_gatt_char.await_args.args[1]
        assert written[5] == 8
        assert len(written) == 16, "8-byte header + 8-byte payload, no extra padding"


class TestWriteCommandHeaderLayout:
    """CLAUDE.md §3 — header byte positions are protocol-critical.

    JC2 firmware silently rejects packets with the wrong magic values
    (per ndeadly commands.md "Command Header" table and confirmed by the
    german77 Wireshark dissector). Pinning the 8 header bytes guards
    against accidental edits to ``write_command``'s ``bytes([...])`` literal.
    """

    def test_header_magic_bytes_pinned(self, client):
        """Every header byte position is fixed by the protocol."""
        asyncio.run(proto.write_command(client, 0x09, 0x07, b"\xAB"))
        hdr = client.write_gatt_char.await_args.args[1]
        assert hdr[0] == 0x09, "byte 0 = command_id"
        assert hdr[1] == 0x91, "byte 1 = 0x91 (Host->Device)"
        assert hdr[2] == 0x01, "byte 2 = 0x01 (Bluetooth transport)"
        assert hdr[3] == 0x07, "byte 3 = subcommand_id"
        assert hdr[4] == 0x00, "byte 4 = reserved (must be 0x00)"
        assert hdr[5] == 1, "byte 5 = len(data)"
        assert hdr[6] == 0x00, "byte 6 = reserved"
        assert hdr[7] == 0x00, "byte 7 = reserved"
        assert hdr[8] == 0xAB, "byte 8 = payload verbatim"

    def test_command_id_and_subcommand_id_vary_independently(self, client):
        """Header bytes 0 and 3 are independent — confirm they take the
        actual function arguments, not swapped or constant."""
        asyncio.run(proto.write_command(client, 0x0A, 0x02, b"\x05"))
        hdr = client.write_gatt_char.await_args.args[1]
        assert hdr[0] == 0x0A
        assert hdr[3] == 0x02

    def test_write_uses_write_command_uuid(self, client):
        """The GATT characteristic UUID must be the canonical write
        characteristic, not the input-report channel or some other UUID."""
        from ble.constants import WRITE_COMMAND_UUID
        asyncio.run(proto.write_command(client, 0x09, 0x07, b"\x01"))
        uuid_arg = client.write_gatt_char.await_args.args[0]
        assert uuid_arg == WRITE_COMMAND_UUID


class TestFeatureMaskDefault:
    """CLAUDE.md §3 #2 — ``FEATURE_MASK_DEFAULT`` must remain ``0xFF``.

    Historical bug: v0.2.12 trimmed to ``0x33`` (Button + Stick + Mouse +
    Rumble) on a battery-saving theory; the JC2 firmware silently
    rejected the trimmed mask (LEDs stuck in pairing-cycle, no vibration,
    mouse data zeroed). v0.6.0 reverted to ``0xFF`` after hardware
    testing, matching the ``coffincolors/jc2mouse`` Linux driver.

    A future "optimization" PR will tempt this constant again. These
    tests are the tripwire.
    """

    def test_mask_is_0xFF(self):
        """The constant itself — pinned hard at 0xFF."""
        assert FEATURE_MASK_DEFAULT == 0xFF, (
            "CLAUDE.md §3 #2: do NOT trim FEATURE_MASK_DEFAULT — "
            "v0.6.0 hardware-verified at 0xFF; trimmed masks are "
            "silently rejected by JC2 firmware."
        )

    def test_mask_includes_all_named_feature_bits(self):
        """Every named feature bit must be set in the default mask.
        Catches a mutation that flips a single bit without changing the
        overall byte if a future contributor renames the constants."""
        for name, bit in [
            ("FEATURE_BUTTON", FEATURE_BUTTON),
            ("FEATURE_STICK", FEATURE_STICK),
            ("FEATURE_IMU", FEATURE_IMU),
            ("FEATURE_MOUSE", FEATURE_MOUSE),
            ("FEATURE_RUMBLE", FEATURE_RUMBLE),
            ("FEATURE_MAGNETOMETER", FEATURE_MAGNETOMETER),
        ]:
            assert FEATURE_MASK_DEFAULT & bit, (
                f"{name} (0x{bit:02X}) missing from FEATURE_MASK_DEFAULT"
            )
