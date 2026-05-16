# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the latency_trace recorder.

Covers:
* ``record`` accumulates samples into per-span deques.
* Worst-frame context is captured only when a sample sets a new max.
* Aggregation produces correct p50 / p95 / max from the rolling buffer.
* Periodic emission throttles to the configured interval (no log flood).
* ``reset`` clears all buffered state.
* The contextvar default is 0 (so the synchronous-path detection in
  ``osio.mouse.macos._post`` correctly treats "no upstream timestamp"
  as "skip the internal_us recording" rather than dividing by garbage).
"""
import logging

import pytest

import latency_trace


@pytest.fixture(autouse=True)
def fresh_state(caplog):
    """Each test starts with empty buffers AND lets caplog see our INFO
    log lines. The ``applog`` configuration sets ``propagate = False``
    on the ``joyglide`` namespace (so library loggers don't double-print
    our custom format), which means caplog's default root-logger
    listener misses our messages. We temporarily flip propagation back
    on for the duration of the test so caplog at the root level catches
    everything, then restore.
    """
    latency_trace.reset()
    joyglide_log = logging.getLogger("joyglide")
    original_propagate = joyglide_log.propagate
    joyglide_log.propagate = True
    caplog.set_level(logging.INFO)
    yield
    joyglide_log.propagate = original_propagate
    latency_trace.reset()


class TestRecordAccumulates:
    """``record`` populates the per-span deque."""

    def test_first_sample_creates_buffer(self):
        latency_trace.record("test_span", 1234)
        assert "test_span" in latency_trace._buffers
        assert list(latency_trace._buffers["test_span"]) == [1234]

    def test_multiple_samples_appended_in_order(self):
        for ns in (100, 200, 300, 400):
            latency_trace.record("s", ns)
        assert list(latency_trace._buffers["s"]) == [100, 200, 300, 400]

    def test_buffer_capped_at_buffer_size(self):
        """deque maxlen drops oldest when full; protects against unbounded
        memory growth during long sessions."""
        for ns in range(latency_trace._BUFFER_SIZE + 50):
            latency_trace.record("s", ns)
        buf = latency_trace._buffers["s"]
        assert len(buf) == latency_trace._BUFFER_SIZE
        # Oldest 50 were dropped; first kept sample is index 50.
        assert buf[0] == 50

    def test_distinct_spans_get_distinct_buffers(self):
        latency_trace.record("a", 100)
        latency_trace.record("b", 200)
        assert list(latency_trace._buffers["a"]) == [100]
        assert list(latency_trace._buffers["b"]) == [200]


class TestWorstFrameContext:
    """Context is captured ONLY when a sample sets a new all-time max for
    its span — per-sample context capture would dwarf the timestamp cost
    on the hot path."""

    def test_first_sample_with_context_captures_it(self):
        latency_trace.record("s", 1000, {"profile": "gaming"})
        assert latency_trace._max_ns["s"] == 1000
        assert latency_trace._max_ctx["s"] == {"profile": "gaming"}

    def test_larger_sample_replaces_context(self):
        latency_trace.record("s", 1000, {"profile": "gaming"})
        latency_trace.record("s", 5000, {"profile": "dynamic"})
        assert latency_trace._max_ns["s"] == 5000
        assert latency_trace._max_ctx["s"] == {"profile": "dynamic"}

    def test_smaller_sample_does_not_touch_context(self):
        latency_trace.record("s", 5000, {"profile": "dynamic"})
        latency_trace.record("s", 1000, {"profile": "gaming"})
        assert latency_trace._max_ns["s"] == 5000
        assert latency_trace._max_ctx["s"] == {"profile": "dynamic"}

    def test_equal_sample_does_not_replace_context(self):
        """Strict > so ties keep the original context dict (avoids
        churning on no-information replacements)."""
        latency_trace.record("s", 5000, {"profile": "dynamic"})
        latency_trace.record("s", 5000, {"profile": "gaming"})
        assert latency_trace._max_ctx["s"] == {"profile": "dynamic"}

    def test_record_without_context_only_tracks_max_ns(self):
        latency_trace.record("s", 1000)
        latency_trace.record("s", 2000)
        assert latency_trace._max_ns["s"] == 2000
        assert "s" not in latency_trace._max_ctx


class TestEmissionAggregation:
    """``_emit_one`` produces p50, p95, max from the rolling buffer."""

    def test_p50_p95_max_from_known_samples(self, caplog):
        # Insert 100 samples 1000ns..100_000ns; sorted, the median is
        # the 50th element (value 51 in 1-indexed; index 50 in 0-indexed
        # → 51000ns → 51us). p95 = index 95 → 96000ns → 96us.
        # Max = 100us.
        for i in range(1, 101):
            latency_trace._buffers.setdefault(
                "s", __import__("collections").deque(maxlen=200)
            ).append(i * 1000)
        latency_trace._emit_one("s")
        # Inspect the log message via caplog.
        msg = caplog.text
        assert "n=100" in msg
        assert "p50=51us" in msg
        assert "p95=96us" in msg
        assert "max1s=100us" in msg

    def test_emit_includes_alltime_max_and_context(self, caplog):
        latency_trace.record("s", 5000, {"profile": "gaming"})
        latency_trace.record("s", 1000)
        latency_trace._emit_one("s")
        msg = caplog.text
        assert "alltime_max=5us" in msg
        assert "profile=gaming" in msg

    def test_emit_on_single_sample_does_not_crash(self, caplog):
        """Edge case: p95 index calc at n=1 must not IndexError."""
        latency_trace.record("s", 1000)
        latency_trace._emit_one("s")
        # No exception. p50, p95, and max all point at the only sample.
        assert "p50=1us" in caplog.text
        assert "p95=1us" in caplog.text

    def test_emit_on_empty_span_is_noop(self, caplog):
        latency_trace._buffers["s"] = __import__("collections").deque(maxlen=200)
        latency_trace._emit_one("s")
        # No log line emitted.
        assert "latency.s" not in caplog.text


class TestPeriodicEmissionThrottle:
    """``_maybe_emit`` skips work until the throttle interval elapsed —
    keeps log output to roughly 1 line/sec/span at any sample rate."""

    def test_first_call_emits(self, caplog):
        latency_trace.record("s", 1000)
        # First record() also triggers _maybe_emit(); since
        # _last_emit_sec was reset to 0, "now - 0" >> interval → emit fires.
        assert "latency.s" in caplog.text

    def test_immediate_second_call_does_not_emit(self, caplog):
        latency_trace.record("s", 1000)
        caplog.clear()
        # Same monotonic instant — second record's _maybe_emit should skip.
        latency_trace.record("s", 2000)
        assert "latency.s" not in caplog.text

    def test_after_interval_elapsed_emits_again(self, caplog):
        latency_trace.record("s", 1000)
        caplog.clear()
        # Force the throttle clock back to make "now" appear far in
        # the future relative to last emit.
        latency_trace._last_emit_sec -= latency_trace._EMIT_INTERVAL_SEC + 0.1
        latency_trace.record("s", 2000)
        assert "latency.s" in caplog.text


class TestReset:
    """``reset`` returns the module to first-launch state. Used by the
    test fixture above and available to users who want a fresh baseline
    after a config change mid-session."""

    def test_clears_buffers_max_and_context(self):
        latency_trace.record("s", 1000, {"profile": "gaming"})
        latency_trace.record("t", 2000)
        latency_trace.reset()
        assert latency_trace._buffers == {}
        assert latency_trace._max_ns == {}
        assert latency_trace._max_ctx == {}

    def test_reset_resets_throttle_clock(self):
        """After reset, the next record() should emit immediately
        (because _last_emit_sec is back to 0)."""
        latency_trace.record("s", 1000)
        latency_trace.reset()
        # Throttle clock is back at 0 — next record's emit fires.
        # Using a fresh caplog would be cleaner but we re-record + check buffer.
        latency_trace.record("s", 2000)
        assert latency_trace._last_emit_sec > 0  # emission happened


class TestContextVar:
    """The ``bleak_callback_start_ns`` ContextVar defaults to 0 so the
    consumer in ``osio.mouse.macos._post`` can treat 0 as 'no upstream
    timestamp' and skip the internal_us recording on the Dynamic /
    Cinematic pump path."""

    def test_default_is_zero(self):
        assert latency_trace.bleak_callback_start_ns.get() == 0

    def test_set_persists_within_same_context(self):
        latency_trace.bleak_callback_start_ns.set(12345)
        assert latency_trace.bleak_callback_start_ns.get() == 12345
        # Reset to default for next test.
        latency_trace.bleak_callback_start_ns.set(0)
