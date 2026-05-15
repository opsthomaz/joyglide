# SPDX-License-Identifier: GPL-3.0-or-later
"""Optical-sensor parser — converts absolute X/Y readings into deltas
and accumulates them into the engine's per-axis bucket.

Lift-off detection: when the controller isn't on a surface, the firmware
zeros the entire mouse data block (0x10..0x17). Earlier builds used
byte 0x0F (the high byte of the right analog stick) as the validity
sentinel — that was a long-standing bug; both ndeadly's hid_reports.md
and german77's Wireshark dissector confirm 0x14..0x17 as the proper
zero-block check.
"""
import time

from parser.constants import (
    OPT_DEADZONE,
    OPT_LIFTOFF_OFFSET,
    OPT_SURFACE_OFFSET,
    OPT_X_OFFSET,
    OPT_Y_OFFSET,
)
from parser.u16_delta import delta_u16
from user_preferences import settings


def parse(state, data: bytes) -> None:
    """Read mouse X/Y from the report, compute delta, push into accumulator."""
    # Mouse Data block in input report 0x05 spans 0x10-0x17 (8 bytes:
    # X, Y, surface quality, lift-off distance — per ndeadly + german77).
    # Need at least bytes through 0x17 to read the lift-off sentinel.
    if len(data) < 0x18:
        return

    # Lift-off / no-data sentinel. Earlier builds checked byte 0x0F,
    # which is actually the high byte of the *right analog stick*, not a
    # mouse-status byte. The check happened to mostly-work because that
    # byte is rarely zero, but it would also incorrectly skip mouse
    # processing when the user pushed the right stick fully down. Both
    # ndeadly and german77 confirm the proper validity signal: when the
    # sensor isn't on a surface, the controller zeros the entire mouse
    # data block (X, Y, surface quality, lift-off). We OR the four
    # extra bytes (0x14-0x17) — if all zero, the firmware isn't
    # reporting a fresh sample, so skip.
    if (data[OPT_SURFACE_OFFSET] | data[OPT_SURFACE_OFFSET + 1]
            | data[OPT_LIFTOFF_OFFSET] | data[OPT_LIFTOFF_OFFSET + 1]) == 0:
        return

    x_raw = data[OPT_X_OFFSET] | (data[OPT_X_OFFSET + 1] << 8)
    y_raw = data[OPT_Y_OFFSET] | (data[OPT_Y_OFFSET + 1] << 8)

    # Pause source-gate. We DO update last_mouse_pos so the next
    # post-resume delta is computed against the most recent (paused)
    # position — otherwise the cursor would jump by however much the
    # user moved the controller during pause. We do NOT accumulate
    # into _dx_accum (avoids the burst) and do NOT update
    # _last_motion_ts (lets the pump's idle brake fire normally).
    if state.paused:
        state.last_mouse_pos = (x_raw, y_raw)
        return

    # Make sure the pump task is running. Idempotent and safe to call
    # from inside the async BLE callback context.
    state.start_pump()

    prev_x, prev_y = state.last_mouse_pos
    if prev_x is not None and prev_y is not None:
        dx_raw = delta_u16(x_raw, prev_x)
        dy_raw = delta_u16(y_raw, prev_y)

        profile = settings.get("profile", "dynamic")
        disable_accel = settings.get("disable_acceleration", True)

        # Gaming forces deadzone to 0 for maximum precision; other
        # profiles honour the user-configured deadzone setting.
        deadzone = 0 if profile == "gaming" else settings.get("deadzone", OPT_DEADZONE)

        dx: float = float(dx_raw if abs(dx_raw) > deadzone else 0)
        dy: float = float(dy_raw if abs(dy_raw) > deadzone else 0)

        if dx != 0.0 or dy != 0.0:
            if disable_accel or profile == "gaming":
                multiplier = 1.0
            elif profile == "cinematic":
                multiplier = 0.8
            else: # Dynamic
                speed_sq = dx * dx + dy * dy
                accel_level = settings.get("acceleration_level", 2)
                if accel_level == 1:
                    max_mult, divisor = 1.5, 300.0
                elif accel_level == 3:
                    max_mult, divisor = 3.5, 100.0
                else:
                    max_mult, divisor = 2.5, 150.0
                multiplier = min(max_mult, 1.0 + speed_sq / divisor)

            sensitivity = settings.get("sensitivity", 1.0)
            final_dx = dx * multiplier * sensitivity
            final_dy = dy * multiplier * sensitivity

            # Gaming bypass: emit directly on packet receipt without going
            # through the pump accumulator. Saves up to 16.67ms of pump-tick
            # latency (60Hz display) for the lowest possible cursor lag.
            # Pump still runs for stick scroll, just stays empty for motion.
            # (state.paused was already gated at function entry.)
            if profile == "gaming":
                state.input_simulator.mouse_move(final_dx, final_dy)
            else:
                state._dx_accum += final_dx
                state._dy_accum += final_dy
            state._last_motion_ts = time.monotonic()

            # Stash the BLE-frame velocity for the pump's predictor so
            # ticks between BLE packets can extrapolate a small synthetic
            # delta. Only meaningful when settings.motion_prediction_enabled
            # is on; the pump reads this regardless but the per-tick output
            # is gated there.
            #
            # Convert "delta per BLE packet" → "delta per pump tick" by
            # dividing by (BLE period / pump period) ≈ ble_period * pump_hz.
            # Pump period isn't accessible here, but the pump is at display
            # refresh (60-120Hz) and BLE is 33-67Hz — ratio ≈ 1-4 pump
            # ticks per BLE packet. We pass the raw final_d{x,y} per BLE
            # packet and the pump scales by 1/pump_ticks_per_packet.
            state._pred_vx = final_dx
            state._pred_vy = final_dy
            state._motion_seq += 1

    state.last_mouse_pos = (x_raw, y_raw)
