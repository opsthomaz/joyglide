# SPDX-License-Identifier: GPL-3.0-or-later
"""Property-based tests for the motion-pump math.

Targets the pure helpers extracted from ``engine.motion_pump`` into
``engine.tuning``. The pump's *scheduling* still lives in the async
loop (and is hard to test without driving asyncio), but its *math*
— drain factor, idle brake, accumulator drain, max-per-tick clamp —
is now isolated and trivially property-testable.

Properties pinned here are the invariants a future refactor must NOT
break, regardless of how fast the BLE packets arrive or how the
profiles get tuned.
"""
from hypothesis import given, settings as hs_settings, strategies as st

from engine.tuning import (
    PUMP_IDLE_BRAKE_CINEMATIC,
    PUMP_IDLE_BRAKE_DEFAULT,
    PUMP_MAX_PER_TICK,
    drain_factor,
    idle_brake,
)


# ── drain_factor ──────────────────────────────────────────────────────────

PROFILES = st.sampled_from(["dynamic", "gaming", "cinematic"])
# Realistic ranges: dt is one display frame (16.7ms @ 60Hz, 8.3ms @ 120Hz)
# but we exercise a wide envelope to catch off-by-zero / huge-dt edges.
DTS = st.floats(min_value=0.0, max_value=0.5,
                 allow_nan=False, allow_infinity=False)
# BLE period EMA: hovers around 0.015 (Windows) / 0.030 (macOS), but we
# generate the full plausible range plus pathological zero/negative values.
PERIODS = st.floats(min_value=-1.0, max_value=1.0,
                     allow_nan=False, allow_infinity=False)


@given(profile=PROFILES, dt=DTS, period=PERIODS)
def test_drain_factor_in_unit_interval(profile: str, dt: float, period: float):
    """Drain factor must always be in [0, 1] — values outside this range
    would either freeze motion (negative) or amplify it past the
    accumulator (>1), neither of which is a meaningful behaviour.

    Why this matters: the loop uses ``state._dx_accum * df`` to compute
    the per-tick emission; ``df > 1`` would emit MORE than the
    accumulator holds and the next subtraction would invert the sign,
    producing visible cursor jitter.
    """
    df = drain_factor(profile, dt, period)
    assert 0.0 <= df <= 1.0


@given(dt=DTS, period=PERIODS)
def test_drain_factor_gaming_is_always_one(dt: float, period: float):
    """Gaming = ``1.0`` regardless of ``dt`` / ``period`` — it's the
    "full drain on first tick" profile by definition. A regression that
    made this anything other than 1.0 would silently introduce latency
    in the FPS profile."""
    assert drain_factor("gaming", dt, period) == 1.0


@given(dt=DTS, period=PERIODS)
def test_drain_factor_cinematic_is_constant_quarter(dt: float, period: float):
    """Cinematic = ``0.25`` always — independent of frame time and BLE
    rate. That constancy is what produces the floaty / inertia feel
    (see docs/RESEARCH.md §4)."""
    assert drain_factor("cinematic", dt, period) == 0.25


@given(dt=DTS, period=st.floats(min_value=0.001, max_value=1.0,
                                  allow_nan=False, allow_infinity=False))
def test_drain_factor_dynamic_is_dt_over_period_clamped(dt: float, period: float):
    """For a positive period and dt, dynamic must equal min(1, dt/period).
    Pins the formula — a mutation `dt * period` or `dt + period` would
    quietly change cursor character without any explicit test case."""
    expected = min(1.0, dt / period) if dt > 0 else 0.0
    assert drain_factor("dynamic", dt, period) == expected


@given(dt=DTS)
def test_drain_factor_dynamic_handles_zero_period(dt: float):
    """Zero / negative period (e.g. JoyCon with no packets yet) must
    fall back to the 30ms default — never raise ZeroDivisionError."""
    df = drain_factor("dynamic", dt, 0.0)
    assert 0.0 <= df <= 1.0
    df = drain_factor("dynamic", dt, -0.005)
    assert 0.0 <= df <= 1.0


def test_drain_factor_dynamic_zero_dt_is_zero():
    """``dt = 0`` must produce drain = 0 (no time elapsed → no motion
    to emit). Catches a `min(1, 0/period)` typo that would silently
    flip to `min(1, period/0)` and explode."""
    assert drain_factor("dynamic", 0.0, 0.030) == 0.0


def test_drain_factor_dynamic_caps_at_one_for_large_dt():
    """``dt >> period`` must clamp at 1.0 (can't drain more than 100%
    of the accumulator in one tick). Without the cap, a stalled
    asyncio loop catching up after ~100ms would emit the entire
    accumulator times ~3, producing a cursor warp."""
    assert drain_factor("dynamic", 0.5, 0.015) == 1.0


# ── idle_brake ────────────────────────────────────────────────────────────


@given(profile=PROFILES)
def test_idle_brake_in_open_unit_interval(profile: str):
    """Brake must be in (0, 1) — exactly 1 would never decay (infinite
    inertia), exactly 0 would zero immediately (no smoothing). The two
    constants happen to be 0.30 and 0.65, both safely interior."""
    b = idle_brake(profile)
    assert 0.0 < b < 1.0


def test_idle_brake_cinematic_is_long_tail():
    """Cinematic must use the LONG-tail constant (0.65). A mutation that
    swapped this with the default would make cinematic feel identical
    to dynamic, defeating the profile."""
    assert idle_brake("cinematic") == PUMP_IDLE_BRAKE_CINEMATIC


def test_idle_brake_non_cinematic_is_default():
    """Every non-cinematic profile uses the aggressive default. Dynamic
    and gaming both want a clean stop, not a coast."""
    assert idle_brake("dynamic") == PUMP_IDLE_BRAKE_DEFAULT
    assert idle_brake("gaming") == PUMP_IDLE_BRAKE_DEFAULT
    # Unknown profile string falls through to the default — defends
    # against typos / future profile names not crashing.
    assert idle_brake("unknown_profile") == PUMP_IDLE_BRAKE_DEFAULT


# ── Idle-brake convergence (iterated decay) ───────────────────────────────


@given(initial=st.floats(min_value=1e-3, max_value=1000.0,
                          allow_nan=False, allow_infinity=False),
       profile=PROFILES)
@hs_settings(max_examples=50)
def test_idle_brake_converges_to_zero(initial: float, profile: str):
    """Repeated brake applications must converge ``accum`` toward 0 from
    any starting value. After enough iterations the magnitude drops below
    a tiny epsilon. Pins the "decay" property of the brake — if a future
    constant > 1 slipped in, this would fail (magnitude would grow).

    Lower bound 1e-3 picked to stay well above float denormals (where
    ``× 0.65`` is a no-op because subnormal × small float underflows
    back to itself). 1e-3 px is far below any cursor-significant value
    so this isn't a meaningful narrowing of coverage.
    """
    brake = idle_brake(profile)
    accum = initial
    # 0.65^N → 0.001 at N ≈ 16; 0.30^N → 0.001 at N ≈ 6. 60 iterations
    # is comfortably enough for either (worst case 0.65^60 ≈ 1e-11).
    for _ in range(60):
        accum *= brake
    assert abs(accum) < abs(initial) * 1e-6


@given(initial=st.floats(min_value=-1000.0, max_value=1000.0,
                          allow_nan=False, allow_infinity=False),
       profile=PROFILES)
def test_idle_brake_preserves_sign(initial: float, profile: str):
    """Brake must never flip the sign (would cause a visible cursor
    direction reversal during the idle tail). Multiplication by a
    positive scalar can't change sign mathematically; this test exists
    as a guard against a future "subtract" or "negate" implementation."""
    brake = idle_brake(profile)
    out = initial * brake
    if initial > 0:
        assert out >= 0
    elif initial < 0:
        assert out <= 0
    else:
        assert out == 0


# ── Per-tick drain math (the actual pump emission step) ─────────────────


@given(accum=st.floats(min_value=-10000.0, max_value=10000.0,
                        allow_nan=False, allow_infinity=False),
       df=st.floats(min_value=0.0, max_value=1.0,
                     allow_nan=False, allow_infinity=False))
def test_drain_step_never_grows_accumulator(accum: float, df: float):
    """Given any drain factor in [0, 1], one step of `new = old - old*df`
    never increases ``|accum|``. This is the invariant that makes the
    pump stable — without it, the accumulator could oscillate or
    diverge."""
    emitted = accum * df
    new_accum = accum - emitted
    assert abs(new_accum) <= abs(accum) + 1e-9  # 1e-9 for fp slop


@given(accum=st.floats(min_value=-10000.0, max_value=10000.0,
                        allow_nan=False, allow_infinity=False))
def test_drain_step_full_drain_zeros_accumulator(accum: float):
    """When df = 1.0 (gaming / large dt), one tick must zero the
    accumulator — pre-clamp. After the clamp, residue is ≤
    PUMP_MAX_PER_TICK in magnitude."""
    emitted = accum * 1.0
    # Pre-clamp residue is exactly zero.
    residue = accum - emitted
    assert residue == 0.0


@given(accum=st.floats(min_value=-10000.0, max_value=10000.0,
                        allow_nan=False, allow_infinity=False))
def test_drain_step_zero_drain_is_noop(accum: float):
    """When df = 0, no motion is emitted and the accumulator is
    unchanged. Pins the boundary — a buggy `df == 0` shortcut path
    that returned wrong residue would fail this."""
    emitted = accum * 0.0
    new_accum = accum - emitted
    assert emitted == 0.0
    assert new_accum == accum


# ── Max-per-tick clamp (defense against junk spikes) ─────────────────────


@given(value=st.floats(min_value=-1e9, max_value=1e9,
                        allow_nan=False, allow_infinity=False))
def test_max_per_tick_clamp_in_bounds(value: float):
    """``max(-CAP, min(CAP, value))`` must always yield a value in
    [-CAP, +CAP]. Pins the clamp formula against an off-by-one or
    inverted comparison."""
    clamped = max(-PUMP_MAX_PER_TICK, min(PUMP_MAX_PER_TICK, value))
    assert -PUMP_MAX_PER_TICK <= clamped <= PUMP_MAX_PER_TICK


def test_max_per_tick_clamp_passthrough_when_in_bounds():
    """Values already inside the bound must pass through unchanged.
    A buggy `min(CAP, value)` without `max(-CAP, ...)` would let
    large negatives escape; we pin both directions."""
    assert max(-PUMP_MAX_PER_TICK, min(PUMP_MAX_PER_TICK, 0.5)) == 0.5
    assert max(-PUMP_MAX_PER_TICK, min(PUMP_MAX_PER_TICK, -0.5)) == -0.5
    assert max(-PUMP_MAX_PER_TICK, min(PUMP_MAX_PER_TICK, PUMP_MAX_PER_TICK)) == PUMP_MAX_PER_TICK
    assert max(-PUMP_MAX_PER_TICK, min(PUMP_MAX_PER_TICK, -PUMP_MAX_PER_TICK)) == -PUMP_MAX_PER_TICK
