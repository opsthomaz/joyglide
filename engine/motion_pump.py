# SPDX-License-Identifier: GPL-3.0-or-later
"""Display-Hz motion pump — drains the per-axis accumulator into cursor moves.

Runs as an asyncio task on the controller's BLE loop. Wakes on a deadline
synchronized with ``input_simulator.refresh_rate`` (60 Hz on most setups,
120 Hz on ProMotion) and translates the slower-arriving packet bursts
into smooth per-frame motion.

The drain factor adapts to the live BLE packet rate via a packet-period
EMA tracked elsewhere — same code feels right at 33Hz (macOS) and 67Hz
(Windows) without a platform branch.
"""
import time

from engine.predictor import decay_velocity, predicted_step
from engine.tuning import (
    PUMP_IDLE_CUTOFF,
    PUMP_MAX_PER_TICK,
    drain_factor,
    idle_brake,
)
from user_preferences import settings


async def motion_pump(state) -> None:
    """Coroutine — runs forever (or until cancelled) on the BLE loop.

    ``state`` is a ``JoyCon`` instance; we read its accumulators and
    motion timestamps, and call ``state.input_simulator.mouse_move`` /
    ``mouse_scroll`` on each tick. ``state.paused`` is honoured as
    defense-in-depth (parsers already gate at source, but a future
    caller might bypass).
    """
    import asyncio
    period = 1.0 / state.input_simulator.refresh_rate
    next_tick = time.perf_counter()
    last_tick_time = next_tick

    # Predictor state — track the last optical motion sequence number we
    # saw, plus how many consecutive ticks have run with no fresh data.
    # See engine.predictor for the rationale; off by default via setting.
    last_seen_seq = state._motion_seq
    ticks_since_fresh = 0

    while True:
        next_tick += period
        sleep_for = next_tick - time.perf_counter()
        if sleep_for > 0:
            await asyncio.sleep(sleep_for)
        else:
            # Slipped a tick (system load / GC). Resync deadline; don't fire-burst.
            next_tick = time.perf_counter()
            await asyncio.sleep(0)

        now = time.perf_counter()
        dt = now - last_tick_time
        last_tick_time = now

        profile = settings.get("profile", "dynamic")

        idle = (time.monotonic() - state._last_motion_ts) > PUMP_IDLE_CUTOFF
        if idle:
            brake = idle_brake(profile)
            state._dx_accum *= brake
            state._dy_accum *= brake
            state._scroll_x_accum *= brake
            state._scroll_y_accum *= brake

        # Per-profile drain factor — pure, see engine.tuning.drain_factor.
        # Gaming = 1.0 (full drain), cinematic = 0.25 (slow inertia),
        # dynamic = dt / ble_period (adaptive 33Hz/67Hz auto-balance).
        df = drain_factor(profile, dt, state._ble_period_ema)

        # Track whether this pump tick is on a fresh BLE-optical update.
        fresh = state._motion_seq != last_seen_seq
        if fresh:
            last_seen_seq = state._motion_seq
            ticks_since_fresh = 0
        else:
            ticks_since_fresh += 1

        # Optical motion prediction — default off, opt-in via setting.
        # Skipped in gaming profile because gaming bypasses the pump
        # accumulator entirely (see parser.mouse_optical) — predicting on
        # top would add latency in the profile that explicitly doesn't
        # want it.
        #
        # On a "gap" tick (no fresh BLE since last pump tick) the
        # predictor emits a synthetic delta from the previous BLE-frame
        # velocity, scaled to one pump-tick worth of motion, decaying
        # per tick. On a "fresh" tick we let the accumulator drain
        # normally (the new packet IS the truth, no need to guess).
        #
        # The accumulator is touched ONLY when we drain it — predicted
        # motion is independent of the accumulator's contents, so we
        # don't subtract it on emission. Earlier (broken) versions
        # subtracted predicted motion from the accumulator, driving it
        # negative and producing visible direction-flip jitter.
        predicting = (
            settings.get("motion_prediction_enabled", False)
            and profile != "gaming"
            and not fresh
            and ticks_since_fresh > 0
        )

        sx = state._scroll_x_accum * df
        sy = state._scroll_y_accum * df

        if predicting:
            pvx, pvy = predicted_step(state._pred_vx, state._pred_vy,
                                        ticks_since_fresh)
            ble_period = state._ble_period_ema if state._ble_period_ema > 0 else 0.030
            ratio = period / ble_period
            # Predicted per-pump-tick motion REPLACES drain output on
            # gap ticks — accumulator is left untouched, so the next
            # fresh BLE packet still arrives on a clean slate.
            dx = pvx * ratio
            dy = pvy * ratio
            state._pred_vx, state._pred_vy = decay_velocity(
                state._pred_vx, state._pred_vy)
        else:
            dx = state._dx_accum * df
            dy = state._dy_accum * df

        dx = max(-PUMP_MAX_PER_TICK, min(PUMP_MAX_PER_TICK, dx))
        dy = max(-PUMP_MAX_PER_TICK, min(PUMP_MAX_PER_TICK, dy))

        # Pause gate (defense-in-depth — parsers already early-return on
        # pause, so the accumulator shouldn't have anything fresh in it.
        # Kept here in case a future caller bypasses the source-side gate).
        if state.paused:
            continue

        if dx != 0.0 or dy != 0.0:
            state.input_simulator.mouse_move(dx, dy)
            if not predicting:
                # Subtract drain only — predicted motion didn't come from
                # the accumulator, so don't take it back out either.
                state._dx_accum -= dx
                state._dy_accum -= dy

        isx = int(sx)
        isy = int(sy)
        if isx != 0 or isy != 0:
            state.input_simulator.mouse_scroll(isx, isy)
            state._scroll_x_accum -= isx
            state._scroll_y_accum -= isy
