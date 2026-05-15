# SPDX-License-Identifier: GPL-3.0-or-later
"""Feature-flag bitmask for command 0x0C (Feature Select).

Each bit corresponds to a sensor stream that may or may not appear in
input report 0x05. Setting bit 5 (Rumble) is what unlocks the
"Battery Current" field at offset 0x22 of the input report — even though
Rumble itself isn't strictly about battery.

Source of truth: ndeadly/switch2_controller_research/commands.md
(section "Command 0x0C - Feature Select" → "Feature Flags") and
german77/JoyconDriver/switch2/command_handler.lua (FeatureTypes table).
Both confirm the same bit assignments.
"""

FEATURE_BUTTON       = 0x01
FEATURE_STICK        = 0x02
FEATURE_IMU          = 0x04   # accelerometer + gyro — unused in this app
FEATURE_MOUSE        = 0x10   # optical sensor data — JoyCon only
FEATURE_RUMBLE       = 0x20   # also gates battery-current reporting
FEATURE_MAGNETOMETER = 0x80   # unused in this app

# What this app actually consumes.
#
# An earlier revision trimmed this from 0xFF (everything) to
# 0x33 (Button + Stick + Mouse + Rumble) per ndeadly's commands.md, on
# the theory that the Joy-Con would stop powering IMU + magnetometer for
# samples we don't read. That trim was never validated on real hardware.
#
# Validation against the user's actual JC2 (May 2026) showed the
# trimmed mask was silently rejected: LEDs stayed in pairing mode, no
# vibration on connect, mouse data block was zeroed (lift-off check
# rejected everything), but button bytes still streamed. Reverting to
# 0xFF — the value coffincolors/jc2mouse uses (its Linux driver is the
# known-working reference for this protocol) — restored full mouse +
# LED + vibration. The theoretical battery saving wasn't worth the
# silent feature loss.
FEATURE_MASK_DEFAULT = (FEATURE_BUTTON | FEATURE_STICK | FEATURE_IMU |
                        FEATURE_MOUSE | FEATURE_RUMBLE | FEATURE_MAGNETOMETER |
                        0x08 | 0x40)  # = 0xFF; coffincolors-compatible
