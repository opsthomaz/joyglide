# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional latency instrumentation of the BLE → CGEventPost pipeline.

Disabled by default (``settings["latency_trace"] = False``); zero
operational cost when off — the only overhead is a single ``bool``
check on the hot path. When on, captures ``time.perf_counter_ns``
timestamps at three checkpoints and emits aggregated percentiles
once per second via :mod:`applog`.

Three timestamps are taken per event:

* ``t0`` — BLE notification callback entry (set by
  :func:`solo_logic.handle_single_notification`, stored in a
  :mod:`contextvars` so it propagates through synchronous parser
  calls but does NOT leak across asyncio tasks).
* ``t1`` — immediately before ``CGEventPost`` (taken inside
  ``osio.mouse.macos._post``).
* ``t2`` — immediately after ``CGEventPost``.

Two derived spans are recorded:

* ``internal_us`` = ``t1 - t0`` — Joyglide's own pipeline cost. Only
  meaningful for the SYNCHRONOUS Gaming profile, where ``CGEventPost``
  is called from the BLE callback. In Dynamic / Cinematic, the pump
  task fires asynchronously and the ``t0`` contextvar is not visible
  to it, so this span is intentionally not recorded for those.
* ``cgevent_us`` = ``t2 - t1`` — Quartz's own cost of accepting the
  event. Profile-independent. Recorded on every ``_post`` call when
  tracing is on.

Design goals
------------

1. **Off → genuinely zero cost.** One ``settings.get`` check, no
   timestamp allocation, no module work.
2. **On → minimal hot-path overhead.** Two ``perf_counter_ns`` calls
   (~50 ns each on Apple Silicon) plus a ``deque.append`` and an
   amortised emission.
3. **Output is for humans first.** Microsecond resolution, one log
   line per span per second, p50/p95/max with worst-frame context.

Worst-frame context
-------------------

The all-time max sample for each span retains the diagnostic context
present when it was recorded (currently: active motion profile). This
turns outliers into actionable signals — a ``max=2311us`` log line
that says ``profile=dynamic`` points at a different root cause than
one that says ``profile=gaming``.

Running it
----------

Edit ``~/Library/Application Support/joyglide/settings.json`` and set
``"latency_trace": true``. Restart the app. Use the cursor for ~60
seconds. Inspect the log (Console.app for the ``.app`` bundle, stderr
for source runs) — you'll see lines like::

    ⏱  latency.cgevent_us n=64 p50=42us p95=110us max1s=187us alltime_max=204us
    ⏱  latency.internal_us n=33 p50=85us p95=180us max1s=240us alltime_max=311us profile=gaming
"""
import time
from collections import deque
from contextvars import ContextVar
from typing import Any

from applog import get_logger

log = get_logger(__name__)


# ── Contextvar shared with solo_logic ──────────────────────────────
# Set by solo_logic.handle_single_notification at callback entry; read
# by osio.mouse.macos._post if it's running synchronously within the
# same callback (i.e. Gaming profile). Default 0 means "no fresh BLE
# timestamp visible" — Dynamic / Cinematic _post calls from the pump
# task see this default and skip the internal_us recording.
bleak_callback_start_ns: ContextVar[int] = ContextVar(
    "bleak_callback_start_ns", default=0
)


# ── Rolling per-span sample buffers ────────────────────────────────
# deque(maxlen=_BUFFER_SIZE) auto-drops old samples, giving us a
# bounded-memory ring that covers ~3-6 seconds of activity at typical
# BLE rates (33-67 Hz). Enough for a stable percentile estimate without
# growing unbounded.
_BUFFER_SIZE: int = 200
_EMIT_INTERVAL_SEC: float = 1.0

_buffers: dict[str, deque[int]] = {}
_max_ns: dict[str, int] = {}
_max_ctx: dict[str, dict[str, Any]] = {}
_last_emit_sec: float = 0.0


def record(span: str, duration_ns: int, context: dict[str, Any] | None = None) -> None:
    """Record one sample for ``span``. Cheap; designed for hot-path use.

    ``context`` (if provided) is captured ONLY when this sample is the
    new all-time max for the span — that's the case where context
    actually informs diagnosis. Per-sample context capture would dwarf
    the timestamp cost.
    """
    buf = _buffers.get(span)
    if buf is None:
        buf = _buffers[span] = deque(maxlen=_BUFFER_SIZE)
    buf.append(duration_ns)
    # Track all-time worst-frame with context. Strict greater-than so
    # ties don't keep replacing the context dict.
    if duration_ns > _max_ns.get(span, 0):
        _max_ns[span] = duration_ns
        if context is not None:
            _max_ctx[span] = dict(context)
    _maybe_emit()


def _maybe_emit() -> None:
    """Emit aggregated stats per span if the throttle interval elapsed.

    Called from :func:`record`; cheap when not yet due (one ``monotonic``
    + one compare). When emitting, snapshots each buffer into a sorted
    list and pulls p50 / p95 / current-window max + all-time max with
    its captured context.
    """
    global _last_emit_sec
    now = time.monotonic()
    if now - _last_emit_sec < _EMIT_INTERVAL_SEC:
        return
    _last_emit_sec = now
    for span in list(_buffers):
        _emit_one(span)


def _emit_one(span: str) -> None:
    """Render one span's stats as a single log line. Internal helper."""
    buf = _buffers.get(span)
    if not buf:
        return
    samples_us = sorted(s // 1000 for s in buf)
    n = len(samples_us)
    p50 = samples_us[n // 2]
    # p95 with a tiny-sample fallback: at n=1 the index calc would
    # return 0 (which is also samples_us[0]); we just want a value
    # that doesn't crash and reflects what we have.
    p95 = samples_us[min(int(n * 0.95), n - 1)]
    cur_max_us = samples_us[-1]
    alltime_max_us = _max_ns.get(span, 0) // 1000
    ctx = _max_ctx.get(span, {})
    ctx_str = " ".join(f"{k}={v}" for k, v in ctx.items()) if ctx else ""
    log.info(
        f"⏱  latency.{span} n={n} p50={p50}us p95={p95}us "
        f"max1s={cur_max_us}us alltime_max={alltime_max_us}us {ctx_str}".rstrip()
    )


def reset() -> None:
    """Wipe all collected samples + worst-frame state.

    Useful for tests (give each test a clean slate) and for triggering
    a fresh all-time-max baseline after toggling a config that you
    expect to change the latency profile (e.g. switching profile from
    dynamic to gaming mid-session).
    """
    global _last_emit_sec
    _buffers.clear()
    _max_ns.clear()
    _max_ctx.clear()
    _last_emit_sec = 0.0


# Re-exported for convenient ``from latency_trace import record, ...`` usage.
__all__ = ["bleak_callback_start_ns", "record", "reset"]
