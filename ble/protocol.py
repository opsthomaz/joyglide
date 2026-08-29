# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure BLE protocol primitives — write_command and the high-level helpers
that build on it (set_leds, enable_mouse, play_vibration_preset, GATT dump).

Everything here is **stateless** with respect to the app — it only takes
a connected ``BleakClient`` and primitive arguments. No imports from
``settings``, ``command_queue``, ``Player``, or other app-level state.
That keeps the protocol surface easy to test and reuse.

Cross-references:
  * Command-header layout: ndeadly commands.md → "Command Header" section.
  * Feature mask bit list: ``ble.feature_flags`` (sourced from ndeadly +
    german77 dissector — both agree).
"""
import asyncio

from applog import get_logger
from ble.constants import (
    COMMAND_FEATURE_SELECT,
    COMMAND_INITIALISATION,
    COMMAND_LEDS,
    COMMAND_VIBRATION,
    SUBCOMMAND_BT_CANCEL_ADVERTISING,
    SUBCOMMAND_ENABLE_FEATURES,
    SUBCOMMAND_PLAY_VIBRATION_PRESET,
    SUBCOMMAND_SET_FEATURE_MASK,
    SUBCOMMAND_SET_PLAYER_LEDS,
    WRITE_COMMAND_UUID,
)
from ble.feature_flags import FEATURE_MASK_DEFAULT

log = get_logger(__name__)

# 0x1812 = HID-over-GATT service UUID. Its presence on a peripheral lets
# macOS use a tighter LL connection interval (~11.25ms / 88Hz) instead of
# the 30ms / 33Hz floor it imposes on generic BLE peripherals. Joy-Cons
# DON'T expose 0x1812 → we're stuck at 33Hz on macOS. Logged on connect
# when settings["show_gatt_dump"] is on.
_HID_SERVICE_UUID = "00001812-0000-1000-8000-00805f9b34fb"

# Player LED bit patterns 1..8 — reproduces the Switch console's
# cumulative-LED convention so multiplayer setups feel native.
# Reference: https://en-americas-support.nintendo.com/app/answers/detail/a_id/22424
#
# Per dekuNukem's Joy-Con 1 reverse engineering (still applies to JC2):
# the byte is a bitfield where bits 0-3 each control one of the 4
# physical player LEDs (bit 0 = LED 1, bit 1 = LED 2, ...).
#
# Convention for player slots:
#   1 → LED 1 lit                → 0x01
#   2 → LEDs 1+2 lit              → 0x03  ← Tier-S verified on hardware
#   3 → LEDs 1+2+3 lit            → 0x07
#   4 → LEDs 1+2+3+4 lit          → 0x0F
#   5..8 → "creative" combinations from Nintendo's support page (only
#   4 physical LEDs but 8 player slots, so non-cumulative patterns
#   distinguish 5-8 from 1-4 visually).
#
# Hardware validation (May 2026): with JC-R as Player 1 + JC-L as
# Player 2, the JC-L showed LEDs 1+2 lit (matching 0x03) — confirms
# our cumulative convention is what JC2 firmware honours.
#
# Note: the working drivers TheFrano/joycon2cpp and Misaka10571/
# joycon2-connector use a SIMPLER convention (1<<N: 0x01, 0x02,
# 0x04, 0x08 — single LED per player). Both work; ours mirrors the
# Switch console's actual visual presentation.
_LED_PATTERN_BY_PLAYER = {
    1: b'\x01',
    2: b'\x03',
    3: b'\x07',
    4: b'\x0F',
    5: b'\x09',
    6: b'\x05',
    7: b'\x0D',
    8: b'\x06',
}


async def write_command(client, command_id: int, subcommand_id: int, data: bytes = b"") -> None:
    """Send a Joy-Con command via the write characteristic.

    Wire format per ndeadly's commands.md "Command Header" table:
      byte 0  command_id
      byte 1  0x91         (Host → Device)
      byte 2  0x01         (Bluetooth transport; 0x00 = USB)
      byte 3  subcommand_id
      byte 4  0x00         (reserved)
      byte 5  len(data)    (real payload length — must MATCH the actual
                            number of bytes that follow)
      byte 6  0x00         (reserved)
      byte 7  0x00         (reserved)
      byte 8+ data         (exactly len(data) bytes — no padding)

    Earlier builds zero-padded ``data`` to a fixed 8-byte minimum
    "to avoid a historical short-payload crash" (moutella's empirical
    comment). ndeadly's docs and the working coffincolors/jc2mouse
    Linux driver disprove this: LEDs (0x09/0x07) want 8 bytes,
    vibration (0x0A/0x02) wants 4 bytes, feature-select (0x0C) wants
    4 bytes. The pad-to-8 had two consequences:
      * Length byte for short payloads (LEDs:1, vibration:1) was
        WRONG — we said len=1 but transmitted 8 zero-padded bytes.
        The firmware silently rejected the malformed command.
      * The trimmed feature-select mask (0x33) effectively never
        reached the controller, which is why mouse mode wouldn't
        activate even though we "wrote" the enable command.
    Caller is now responsible for passing exactly the bytes the spec
    requires; we send them verbatim.
    """
    header = bytes([command_id, 0x91, 0x01, subcommand_id, 0x00, len(data), 0x00, 0x00])
    # The command characteristic is write-without-response (ndeadly
    # bluetooth_interface.md). Say so explicitly rather than relying on
    # bleak's ``response=None`` auto-detection.
    await client.write_gatt_char(WRITE_COMMAND_UUID, header + data, response=False)


async def cancel_bluetooth_advertising(client) -> None:
    """Stop the JC2's BLE advertising state (cmd 0x03 / sub 0x02).

    Per ndeadly commands.md: "Cancels any active Bluetooth LE
    advertising (though player leds continue to cycle indefinitely,
    maybe a firmware bug?)".

    On reconnect via the JC2 sync button, the controller is still in
    advertising mode when our app picks it up — and the LED cycle
    inherited from that state can override the player-LED pattern
    we then send. Calling this command first transitions the firmware
    out of "advertising / waiting for host" so subsequent set_leds /
    enable_mouse writes actually take effect.

    Empty payload — request data is "None" per the spec.
    """
    await write_command(client, COMMAND_INITIALISATION, SUBCOMMAND_BT_CANCEL_ADVERTISING, b"")


async def play_vibration_preset(client, preset_id: int) -> None:
    """Play one of the documented vibration presets (preset_id 0x01..0x07).

    Payload is 4 bytes per ndeadly's example: ``preset_id`` in byte 0,
    three zero bytes after. The trailing zeros are part of the payload
    (length byte = 0x04), not free padding.
    """
    payload = bytes([preset_id, 0x00, 0x00, 0x00])
    await write_command(client, COMMAND_VIBRATION, SUBCOMMAND_PLAY_VIBRATION_PRESET, payload)


async def set_leds(client, player_number: int) -> None:
    """Set the player-LED pattern. Caps at slot 8.

    Payload is 8 bytes per ndeadly's example: pattern byte in byte 0,
    seven zeros after. The trailing zeros are part of the payload
    (length byte = 0x08), not free padding.
    """
    player_number = min(player_number, 8)
    pattern = _LED_PATTERN_BY_PLAYER[player_number]
    payload = pattern + b"\x00" * (8 - len(pattern))
    await write_command(client, COMMAND_LEDS, SUBCOMMAND_SET_PLAYER_LEDS, payload)


async def enable_mouse(client) -> None:
    """Activate the optical sensor + button + stick + battery-current stream.

    Two writes are required (per ndeadly commands.md, validated against
    german77's Wireshark dissector):

      1. ``COMMAND_FEATURE_SELECT / SUBCOMMAND_SET_FEATURE_MASK`` — declares
         which features the host is willing to receive in input report 0x05.
         Without this prior call, Enable/Disable subcommands are no-ops.
      2. ``COMMAND_FEATURE_SELECT / SUBCOMMAND_ENABLE_FEATURES`` — turns
         the masked features on.

    History: v0.2.12 trimmed FEATURE_MASK_DEFAULT from ``0xFF``
    (everything) to ``0x33`` (Button + Stick + Mouse + Rumble) on the
    theory that the JC2 firmware would stop powering IMU + Magnetometer
    when not requested — saving controller battery. **v0.6.0 reverted
    to ``0xFF``** after hardware testing: the trimmed 0x33 mask was
    silently rejected by JC2 firmware (LEDs stayed in pairing-cycle,
    no vibration, mouse data zeroed). The ``coffincolors/jc2mouse``
    Linux driver uses ``0xFF`` and that's the empirically-validated
    path. We follow.

    The IMU bit (0x04) is present in 0xFF, so our default mask already
    enables IMU — no per-setting OR needed (the previous
    ``if imu_enabled: mask |= FEATURE_IMU`` was a no-op since the bit was
    already set). The ``settings["imu_enabled"]`` setting separately
    controls whether parser/imu.py *parses* the bytes — the controller
    streams them either way under 0xFF.

    Calibration scales (4096 = 1 G accel, 48000 = 360° gyro,
    25 + raw/127 °C temperature) confirmed in
    github.com/german77/JoyconDriver#1; timestamp scale corrected to
    1 MHz (not 50 kHz) via hardware verification — see parser/imu.py.
    """
    mask = FEATURE_MASK_DEFAULT
    payload = bytes([mask, 0x00, 0x00, 0x00])
    await write_command(client, COMMAND_FEATURE_SELECT, SUBCOMMAND_SET_FEATURE_MASK, payload)
    await asyncio.sleep(0.5)
    await write_command(client, COMMAND_FEATURE_SELECT, SUBCOMMAND_ENABLE_FEATURES, payload)


async def dump_gatt_profile(client) -> None:
    """Diagnostic helper: log every GATT service and characteristic of the
    connected controller. Used when ``settings["show_gatt_dump"] = True``,
    typically while reverse-engineering a new controller variant.
    """
    log.info("========= GATT PROFILE =========")
    has_hid = False
    for svc in client.services:
        marker = " ← HID!" if svc.uuid.lower() == _HID_SERVICE_UUID else ""
        if svc.uuid.lower() == _HID_SERVICE_UUID:
            has_hid = True
        log.info(f"  SERVICE  {svc.uuid}{marker}")
        for char in svc.characteristics:
            props = ",".join(char.properties)
            log.info(f"    CHAR   {char.uuid}  [{props}]")
    log.info(
        f"  HID over GATT (0x1812): "
        f"{'PRESENT ✓ → macOS may accept 11.25ms' if has_hid else 'ABSENT ✗ → macOS caps at 30ms'}"
    )
    log.info("================================")
