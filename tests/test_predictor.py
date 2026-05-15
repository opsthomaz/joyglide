# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for ``engine.predictor`` — optical-velocity prediction helpers.

The predictor's job is to emit synthetic per-tick deltas on pump frames
that have no fresh BLE optical update, smoothing the visible cursor
motion when display refresh > BLE rate (always true). Both functions
are pure and trivially unit-testable.

Pinned invariants:
  * Prediction stops after ``PRED_MAX_TICKS_AHEAD`` so a stalled BLE
    stream can't drift the cursor off-screen.
  * Decay is < 1, so iterated application converges to zero.
  * Decay preserves sign — the cursor never reverses direction during
    the predicted tail.
"""
from hypothesis import given, strategies as st

from engine.predictor import (
    PRED_DECAY,
    PRED_MAX_TICKS_AHEAD,
    decay_velocity,
    predicted_step,
)


# ── predicted_step ───────────────────────────────────────────────────────


@given(vx=st.floats(min_value=-100.0, max_value=100.0,
                      allow_nan=False, allow_infinity=False),
       vy=st.floats(min_value=-100.0, max_value=100.0,
                      allow_nan=False, allow_infinity=False),
       n=st.integers(min_value=0, max_value=PRED_MAX_TICKS_AHEAD))
def test_predicted_step_inside_window_returns_velocity(vx: float, vy: float, n: int):
    """While ``n`` is within the cap, predicted_step must return the
    velocity unchanged. Pins the "honour the velocity until the safety
    cap" behaviour."""
    out_vx, out_vy = predicted_step(vx, vy, n)
    assert (out_vx, out_vy) == (vx, vy)


@given(vx=st.floats(min_value=-100.0, max_value=100.0,
                      allow_nan=False, allow_infinity=False),
       vy=st.floats(min_value=-100.0, max_value=100.0,
                      allow_nan=False, allow_infinity=False),
       n=st.integers(min_value=PRED_MAX_TICKS_AHEAD + 1, max_value=1000))
def test_predicted_step_above_cap_returns_zero(vx: float, vy: float, n: int):
    """Once ``n > PRED_MAX_TICKS_AHEAD``, predicted_step must return
    (0, 0) regardless of velocity. Defends against runaway cursor
    drift on a stalled BLE stream."""
    assert predicted_step(vx, vy, n) == (0.0, 0.0)


def test_predicted_step_boundary_exact_cap_still_emits():
    """At n == PRED_MAX_TICKS_AHEAD (the boundary), prediction should
    still emit (>cap is the cutoff, not >=). Pins the comparison
    direction — easy to flip."""
    assert predicted_step(1.5, 2.5, PRED_MAX_TICKS_AHEAD) == (1.5, 2.5)
    assert predicted_step(1.5, 2.5, PRED_MAX_TICKS_AHEAD + 1) == (0.0, 0.0)


# ── decay_velocity ───────────────────────────────────────────────────────


@given(vx=st.floats(min_value=-100.0, max_value=100.0,
                      allow_nan=False, allow_infinity=False),
       vy=st.floats(min_value=-100.0, max_value=100.0,
                      allow_nan=False, allow_infinity=False))
def test_decay_velocity_shrinks_magnitude(vx: float, vy: float):
    """One step of decay must never grow ``|v|``. Pinned because
    PRED_DECAY < 1 by design — a regression to >= 1 (which would amplify)
    is a loud cursor bug."""
    out_vx, out_vy = decay_velocity(vx, vy)
    assert abs(out_vx) <= abs(vx) + 1e-9
    assert abs(out_vy) <= abs(vy) + 1e-9


@given(vx=st.floats(min_value=-100.0, max_value=100.0,
                      allow_nan=False, allow_infinity=False),
       vy=st.floats(min_value=-100.0, max_value=100.0,
                      allow_nan=False, allow_infinity=False))
def test_decay_velocity_preserves_sign(vx: float, vy: float):
    """Decay must never flip the sign — cursor reversing direction
    during the predicted tail would be a visible bug. Multiplication
    by a positive scalar can't change sign mathematically; this guards
    against a future "subtract" or "negate" implementation."""
    out_vx, out_vy = decay_velocity(vx, vy)
    if vx > 0: assert out_vx >= 0
    if vx < 0: assert out_vx <= 0
    if vy > 0: assert out_vy >= 0
    if vy < 0: assert out_vy <= 0


@given(initial=st.floats(min_value=1e-3, max_value=100.0,
                          allow_nan=False, allow_infinity=False))
def test_decay_velocity_converges_to_zero(initial: float):
    """Iterated decay must converge to ~0 within a bounded number of
    steps — otherwise the predicted tail would never resolve and a
    series of motions would compound forever.

    Bounded to ``initial >= 1e-3`` because below that we'd hit float
    denormals where ``× 0.85`` is a no-op (subnormal × small float
    underflows back to itself). A 1e-3 pixel velocity is well below
    any physically meaningful cursor speed; the realistic range is
    1.0–100.0 px / BLE-frame.
    """
    vx, vy = initial, initial
    # 100 ticks at 0.85 → 0.85^100 ≈ 7e-8, comfortably below epsilon.
    for _ in range(100):
        vx, vy = decay_velocity(vx, vy)
    assert abs(vx) < abs(initial) * 1e-6
    assert abs(vy) < abs(initial) * 1e-6


def test_decay_constant_is_in_unit_interval():
    """PRED_DECAY must be in (0, 1) — exactly 1 = no decay (forever
    coast); 0 = single-tick prediction only (defeats the purpose);
    > 1 = amplification (cursor explodes); < 0 = sign-flip (cursor
    reverses). The 0.85 picked empirically should stay in (0, 1)."""
    assert 0.0 < PRED_DECAY < 1.0
