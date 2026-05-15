# SPDX-License-Identifier: GPL-3.0-or-later
"""Optical motion predictor — pure helper for the pump.

The motion pump runs at display refresh (60/120 Hz) but BLE packets
only arrive every ~30 ms (macOS) / ~15 ms (Windows). Between packets
the accumulator drains on what was last received, so cursor speed
visibly steps down between BLE arrivals.

This predictor takes the last-known optical velocity (deltas from the
most recent BLE packet) and extrapolates a small synthetic delta on
each pump tick that has no fresh data, smoothing the motion until the
next packet arrives. It is a textbook "client-side prediction" /
"dead reckoning" technique used in input drivers and netcode.

Why we ship this rather than IMU-based prediction:
  * Input report 0x05 carries one IMU sample per packet at the same
    rate as the optical sensor — so IMU samples cannot reduce
    inter-packet latency on this report (see ``parser.imu`` for the
    full honest write-up).
  * Optical-velocity prediction uses data we already have, with no
    new sensor calibration to verify, and the worst case is "the
    cursor coasts a bit too far on a sudden stop" which the pump's
    idle brake (``engine.tuning.idle_brake``) corrects within ~50 ms.

Tunables:
  * ``PRED_DECAY`` — the predicted velocity decays by this factor per
    pump tick. Without decay, prediction would integrate forever and
    produce visible drift. With it, an unpredicted cursor stop coasts
    for a few ticks then stops on its own. 0.85 picked empirically;
    means the predicted contribution falls off ~7×/100ms at 60Hz pump.
  * ``PRED_MAX_TICKS_AHEAD`` — hard cap on how many ticks-without-fresh
    -data we'll predict for. Defends against a stalled BLE stream
    extrapolating arbitrarily far.

Default OFF — opt-in via ``settings["motion_prediction_enabled"]``.
The pump's existing dt-proportional drain already handles most of the
smoothing; prediction is the extra polish for users who want it.
"""

# Per-tick decay applied to the predicted velocity. The predicted
# contribution shrinks each frame so an unpredicted stop comes to rest
# rather than coasting forever. 0.85 → 50% in ~5 ticks (~80ms at 60Hz)
# → 10% in ~14 ticks (~230ms). Aggressive enough to feel responsive,
# gentle enough to be invisible during steady motion.
PRED_DECAY = 0.85

# Hard cap on consecutive predicted ticks. If BLE truly stalls (lost
# packets, sleep, range fade), we'd otherwise extrapolate forever and
# the cursor would slide off-screen. After this many ticks of "no fresh
# data" the predictor zeros itself.
PRED_MAX_TICKS_AHEAD = 8


def predicted_step(prev_vx: float, prev_vy: float,
                    ticks_since_fresh: int) -> tuple[float, float]:
    """Compute the synthetic per-tick delta to add when no fresh BLE
    packet has arrived since the last pump tick.

    Pure function — takes the previous predicted velocity and the
    number of ticks since fresh data, returns the per-tick (dx, dy)
    contribution. The caller is responsible for storing the decayed
    velocity for next call (see ``decay_velocity`` below).

    Returns ``(0.0, 0.0)`` once ``ticks_since_fresh`` exceeds
    ``PRED_MAX_TICKS_AHEAD`` so a stalled BLE stream can't drift the
    cursor indefinitely.
    """
    if ticks_since_fresh > PRED_MAX_TICKS_AHEAD:
        return 0.0, 0.0
    return prev_vx, prev_vy


def decay_velocity(vx: float, vy: float) -> tuple[float, float]:
    """Apply one tick of decay to the predicted velocity.

    Multiplies by ``PRED_DECAY`` (< 1) so iterated application
    converges to zero. Same shape as ``engine.tuning.idle_brake``,
    different scope: idle brake decays the *accumulator* (motion
    we received but haven't emitted yet); this decays the *predicted
    velocity* (synthetic motion we're inventing for between-packet
    smoothness).
    """
    return vx * PRED_DECAY, vy * PRED_DECAY
