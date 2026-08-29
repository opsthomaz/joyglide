# SPDX-License-Identifier: GPL-3.0-or-later
"""Joy-Con 2 BLE protocol constants — manufacturer ID, GATT UUIDs, command IDs.

Cross-referenced with:
  * ``ndeadly/switch2_controller_research/bluetooth_interface.md`` (UUIDs)
  * ``ndeadly/switch2_controller_research/commands.md`` (command IDs + headers)
  * ``german77/JoyconDriver/switch2/command_handler.lua`` (Wireshark dissector)

See ``docs/ARCHITECTURE.md`` for the authoritative protocol overview.
"""

# ── Joy-Con 2 BLE advertising signature ─────────────────────────────────
# Nintendo's BLE manufacturer ID (assigned in the Bluetooth SIG registry).
# Switch 2 controllers always advertise this in the manufacturer-specific
# data field of their BLE adverts; we use it as the discriminator during
# scan to distinguish JC2 from any other BLE peripheral nearby.
JOYCON_MANUFACTURER_ID = 1363

# First 4 bytes of the manufacturer-specific data block. Stable across
# JC2 firmwares; we match this prefix to filter for JC2 specifically.
# Note: coffincolors/jc2mouse extends the prefix to 5 bytes (adding 0x05)
# and reads byte 5 as a controller-type subtype (0x69 = Pro Controller 2).
# We could narrow our filter to detect JC2 vs Pro vs GC NSO in the future
# — see "More controller types" in the README contribution ideas.
JOYCON_MANUFACTURER_PREFIX = bytes([0x01, 0x00, 0x03, 0x7E])

# ── GATT characteristic UUIDs (the ones we actually use) ────────────────
# Common input-report channel — all Switch 2 controller types stream the
# 0x05 report (buttons + sticks + mouse + battery + IMU) here at the
# OS-negotiated LL connection interval.
INPUT_REPORT_UUID = "ab7de9be-89fe-49ad-828f-118f09df7fd2"

# Side-specific input report channels. Each side has its own GATT
# characteristic for input reports 0x07 (JC-L) / 0x08 (JC-R). These
# carry a "Power Info" bitfield at offset 0x1 with the firmware's own
# battery-level estimate (0–9, 4-bit field) — the same value the Switch
# console reads to draw its battery icon. We only read this one field;
# the rest of the report (relative mouse, multi-sample motion, etc.) is
# either redundant with input report 0x05 or in an unknown-packed format
# per ndeadly.
#
# Source: research/ndeadly_switch2/hid_reports.md — Input Report 0x07
# (JC-L, GATT handle 0x000E) / Input Report 0x08 (JC-R, GATT handle
# 0x0011).
INPUT_REPORT_JCL_UUID = "cc1bbbb5-7354-4d32-a716-a81cb241a32a"
INPUT_REPORT_JCR_UUID = "d5a9e01e-2ffc-4cca-b20c-8b67142bf442"

# Command channel — host writes here to enable features, set LEDs, trigger
# vibration, etc. Advertises ``write`` (with response) in our GATT dump —
# see docs/ARCHITECTURE.md "GATT Profile"; bleak picks the write mode
# from the advertised properties.
WRITE_COMMAND_UUID = "649d4ac9-8eb7-4e6c-af44-1ea54fe5f005"

# ── Command IDs (high-level command catalog) ────────────────────────────
COMMAND_INITIALISATION  = 0x03  # BT wake/cancel, pairing info, USB init, etc.
COMMAND_LEDS            = 0x09  # set the player-LED pattern (1–8 player slots)
COMMAND_VIBRATION       = 0x0A  # play a haptic preset or send raw rumble data
COMMAND_FEATURE_SELECT  = 0x0C  # set/enable/disable which features stream in input report 0x05

# ── Subcommand IDs ──────────────────────────────────────────────────────
SUBCOMMAND_SET_PLAYER_LEDS       = 0x07
SUBCOMMAND_PLAY_VIBRATION_PRESET = 0x02
# Feature Select subcommands (per ndeadly commands.md + german77 dissector)
SUBCOMMAND_SET_FEATURE_MASK = 0x02  # required before Enable/Disable can affect anything
SUBCOMMAND_ENABLE_FEATURES  = 0x04
# Initialisation subcommands (per ndeadly commands.md)
SUBCOMMAND_BT_CANCEL_ADVERTISING = 0x02   # stop any in-flight BLE advertising
