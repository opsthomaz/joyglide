# SPDX-License-Identifier: GPL-3.0-or-later
"""Pump tuning constants and the pure helpers that consume them.

Constants picked empirically from feel-testing on macOS (33Hz BLE,
60Hz display) and Windows (67Hz BLE, 60/120Hz display). Touching any
of these will perceptibly change cursor character.

The two helpers (``drain_factor``, ``idle_brake``) live here rather
than in ``motion_pump`` because they're pure (no I/O, no state
mutation) — keeping them out of the async loop body makes them
trivially property-testable, and lets the loop in ``motion_pump`` stay
focused on scheduling.
"""

# Seconds without motion before idle brake activates.
PUMP_IDLE_CUTOFF = 0.060

# Idle brake: once PUMP_IDLE_CUTOFF has elapsed without new motion, multiply
# the accumulator by this factor each tick. < 1 = decay; closer to 1 = longer
# inertia tail. Per profile:
#   dynamic / gaming → 0.30 (keep 30% per tick, decay 70%)  → stops in ~50ms
#   cinematic        → 0.65 (keep 65% per tick, decay 35%)  → long tail, real inertia
PUMP_IDLE_BRAKE_DEFAULT   = 0.30
PUMP_IDLE_BRAKE_CINEMATIC = 0.65

# Hard cap on the per-tick cursor delta (in pixels). Defends against junk
# spikes from the optical sensor or a BLE backlog after a stall.
PUMP_MAX_PER_TICK = 200

# Accumulator magnitude (pixels) below which idle decay snaps to exactly
# 0.0. The idle brake shrinks the accumulator geometrically but never
# reaches zero on its own — hardware capture (2026-08-29) showed the pump
# posting 60 zero-pixel CGEvents/s for ~10 s after every stop, until float
# underflow. 1/20 px is far below anything a display can show.
PUMP_SETTLE_PX = 0.05

# Default BLE inter-packet period when the EMA hasn't converged yet (or is
# pathologically small). 30ms ≈ macOS floor, the more conservative of the
# two real platforms — picking the slow one avoids a momentary "drains too
# hard" burst when the EMA is initialising.
_DEFAULT_BLE_PERIOD = 0.030


def drain_factor(profile: str, dt: float, ble_period_ema: float) -> float:
    """Return the per-tick drain factor (∈ [0, 1]) for ``profile``.

    The drain factor is what fraction of the per-axis accumulator is
    emitted as cursor motion this tick. The three profiles each pick
    a different curve:

      * **gaming** — always ``1.0``. Full drain on the first tick after
        a packet → 1:1 raw, lowest possible latency.
      * **cinematic** — constant ``0.25``. Slow drain regardless of
        frame time → motion spreads across many frames, producing
        visible inertia that hides hand tremor.
      * **dynamic** (default) — ``dt / ble_period_ema``, clamped to
        ``1.0``. Adaptive: macOS at ~30ms BLE drains ~55% per 60Hz
        tick; Windows at ~15ms BLE drains 100% (clamped). Same code
        feels right at both rates.

    A zero or negative ``ble_period_ema`` is treated as the default
    (30ms) so a freshly-initialised JoyCon doesn't divide by zero on
    the first packet.
    """
    if profile == "gaming":
        return 1.0
    if profile == "cinematic":
        return 0.25
    period = ble_period_ema if ble_period_ema > 0 else _DEFAULT_BLE_PERIOD
    return min(1.0, dt / period) if dt > 0 else 0.0


def idle_brake(profile: str) -> float:
    """Per-tick brake multiplier applied to accumulators during idle.

    Non-cinematic profiles decay aggressively (keep 30% per tick →
    stops in ~50ms); cinematic keeps 65% per tick for a deliberate
    inertia tail. Both values are < 1 so iterated application
    converges to zero.
    """
    return PUMP_IDLE_BRAKE_CINEMATIC if profile == "cinematic" else PUMP_IDLE_BRAKE_DEFAULT


def settle_accumulator(value: float) -> float:
    """Snap a sub-visible accumulator value to exactly ``0.0``.

    Applied after the idle brake each tick so a stopped controller stops
    producing events within a few ticks instead of emitting a ~10 s tail
    of zero-pixel moves. Values at or above ``PUMP_SETTLE_PX`` in
    magnitude pass through unchanged.
    """
    return 0.0 if -PUMP_SETTLE_PX < value < PUMP_SETTLE_PX else value
