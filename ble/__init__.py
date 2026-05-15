# SPDX-License-Identifier: GPL-3.0-or-later
"""BLE layer — Joy-Con 2 Bluetooth Low Energy protocol + connection lifecycle.

Three submodules:

  * ``ble.constants`` — UUIDs, manufacturer ID, command/subcommand IDs.
  * ``ble.feature_flags`` — feature-mask bit constants for command 0x0C.
  * ``ble.protocol`` — pure write helpers (write_command, set_leds,
    enable_mouse, vibration, GATT dump). No app-level dependencies.
  * ``ble.connection`` — BLE scan + connect + reconnect orchestration.
    Depends on app-level concerns (settings, command_queue, Player).

Most callers only need ``ble.protocol`` (to send commands) or
``ble.connection`` (to wire up a controller). The constants are exported
from this package root for convenience.
"""
from ble.constants import (
    INPUT_REPORT_JCL_UUID,
    INPUT_REPORT_JCR_UUID,
    INPUT_REPORT_UUID,
    JOYCON_MANUFACTURER_ID,
    JOYCON_MANUFACTURER_PREFIX,
    WRITE_COMMAND_UUID,
    COMMAND_FEATURE_SELECT,
    COMMAND_LEDS,
    COMMAND_VIBRATION,
    SUBCOMMAND_ENABLE_FEATURES,
    SUBCOMMAND_PLAY_VIBRATION_PRESET,
    SUBCOMMAND_SET_FEATURE_MASK,
    SUBCOMMAND_SET_PLAYER_LEDS,
)
from ble.feature_flags import (
    FEATURE_BUTTON,
    FEATURE_IMU,
    FEATURE_MAGNETOMETER,
    FEATURE_MASK_DEFAULT,
    FEATURE_MOUSE,
    FEATURE_RUMBLE,
    FEATURE_STICK,
)

__all__ = [
    "COMMAND_FEATURE_SELECT",
    "COMMAND_LEDS",
    "COMMAND_VIBRATION",
    "FEATURE_BUTTON",
    "FEATURE_IMU",
    "FEATURE_MAGNETOMETER",
    "FEATURE_MASK_DEFAULT",
    "FEATURE_MOUSE",
    "FEATURE_RUMBLE",
    "FEATURE_STICK",
    "INPUT_REPORT_JCL_UUID",
    "INPUT_REPORT_JCR_UUID",
    "INPUT_REPORT_UUID",
    "JOYCON_MANUFACTURER_ID",
    "JOYCON_MANUFACTURER_PREFIX",
    "SUBCOMMAND_ENABLE_FEATURES",
    "SUBCOMMAND_PLAY_VIBRATION_PRESET",
    "SUBCOMMAND_SET_FEATURE_MASK",
    "SUBCOMMAND_SET_PLAYER_LEDS",
    "WRITE_COMMAND_UUID",
]
