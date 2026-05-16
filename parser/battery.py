# SPDX-License-Identifier: GPL-3.0-or-later
"""Battery parser — voltage, charge state, and current.

Voltage and charge byte are always present in input report 0x05.
Battery current is only populated when feature bit 5 (Rumble) is enabled
in the feature mask — we set it by default (see ``ble.feature_flags``).
"""
import time

from applog import get_logger
from parser.constants import (
    BATTERY_CHARGE_OFFSET,
    BATTERY_CURRENT_DIVISOR,
    BATTERY_CURRENT_OFFSET,
    BATTERY_VOLTAGE_OFFSET,
)
from user_preferences import settings

log = get_logger(__name__)


def parse(state, data: bytes) -> None:
    """Update ``state.battery_*`` fields from input report 0x05 bytes.

    ``state`` is a ``JoyCon`` instance; we read its ``_battery_last_ts``
    (used as a 1-Hz throttle) and write back ``battery_mv``,
    ``battery_pct``, ``battery_charging``, ``battery_full``, and
    ``battery_current_ma``. No-op if the packet is too short or if
    less than a second has elapsed since the last update.
    """
    # Need at least the voltage + charge bytes; current is a separate
    # gate later. ``BATTERY_CURRENT_OFFSET = 0x22`` is the first byte
    # past the always-present fields, so the packet must reach it.
    if len(data) < BATTERY_CURRENT_OFFSET:
        return
    now = time.monotonic()
    if now - state._battery_last_ts < 1.0:
        return
    mv = data[BATTERY_VOLTAGE_OFFSET] | (data[BATTERY_VOLTAGE_OFFSET + 1] << 8)
    # Implausibility filter — discard junk before sensors stabilize.
    if mv < 2500 or mv > 5000:
        return
    state._battery_last_ts = now
    state.battery_mv = mv
    # Voltage-based percentage — used as a fallback when the firmware
    # battery level (read from the side-specific input report 0x07/0x08
    # via parser.power_info) is unavailable. Linear approximation:
    # 3300 mV ≈ 0%, 4200 mV ≈ 100%. LiPo is non-linear in reality —
    # plateaus near 3.7 V for most of the discharge — so this is
    # systematically wrong by up to ~15% in mid-range. The firmware
    # value, when subscribed, is authoritative.
    if getattr(state, "battery_pct_source", "voltage") != "firmware":
        state.battery_pct = max(0, min(100, round((mv - 3300) * 100 / 900)))
        # Charge state byte interpretation. ndeadly hid_reports.md only
        # documents:
        #   - 0x20 = "fully charged"
        #   - "rises and settles on 0x34" while charging via USB
        # 0x00 = on battery is OUR empirical finding (818 s hardware
        # capture: byte stayed at 0 throughout on-battery use). The
        # "non-zero, non-0x20 = charging at some rate" rule is also
        # extrapolated empirically from the 0x34 example — held in
        # our testing but may need refinement if a future contributor
        # observes other charge-byte values.
        charge_byte = data[BATTERY_CHARGE_OFFSET]
        state.battery_full     = charge_byte == 0x20
        state.battery_charging = charge_byte != 0 and charge_byte != 0x20
    else:
        # Firmware path is active — leave battery_pct / battery_full /
        # battery_charging alone (parser.power_info owns those). We
        # still update battery_mv as informational.
        pass

    # "Battery Current?" — populated when FEATURE_RUMBLE is in the
    # feature mask (it is by default). ndeadly's docs label this field
    # with a ? because the unit was unconfirmed.
    #
    # Two independent sources converge on raw / 100 = mA:
    #
    # 1. TropicalCyclone/switch2-controller-driver (working PC driver)
    #    reads `battery_current = decodeu(data[33:35]) / 100`.
    #
    # 2. 818-second hardware capture on a JC2 (R) at 30% / 3573 mV idle
    #    over BLE (May 2026), 800 samples — applying /100:
    #      - Range 18.01..18.82 mA (matches JC2's 525 mAh / 20 h ≈
    #        26 mA spec to within idle / active variation)
    #      - Pearson correlation with voltage: r=+0.967 (consistent
    #        with fixed-resistance load: I = V / R)
    #      - Voltage discharge rate (-0.73 mV/min) projects to ~21 h
    #        full→empty runtime, matching Nintendo's spec
    #
    # We store the scaled value (float, mA) as ``state.battery_current_ma``
    # — the field name now actually means what it says. Sign convention
    # is unconfirmed; values observed have always been positive on
    # battery, so charging-mode polarity is still TBD on hardware that
    # can charge the JC2 over the rail without breaking BLE.
    if len(data) >= BATTERY_CURRENT_OFFSET + 2:
        raw = data[BATTERY_CURRENT_OFFSET] | (data[BATTERY_CURRENT_OFFSET + 1] << 8)
        state.battery_current_ma = raw / BATTERY_CURRENT_DIVISOR
        # Diagnostic log — when ``battery_log`` is on, emit a one-line
        # entry per 1 Hz parser tick with mv/pct/mA + raw u16. Lets us
        # double-check the /100 scale on different SoC ranges.
        if settings.get("battery_log", False):
            log.info(
                f"  🔋 battery t={now:.2f} mv={state.battery_mv} "
                f"pct={state.battery_pct} mA={state.battery_current_ma:.2f} "
                f"raw={raw}"
            )
