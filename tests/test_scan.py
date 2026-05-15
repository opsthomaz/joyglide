# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for ``ble.connection.scan_device`` — the BLE advertising scan.

Focus on the failure modes that produced the "looks like syncing but
isn't" symptom:

  * Timeout returns ``None`` (instead of hanging forever).
  * Status events are pushed to ``command_queue`` so the UI mirrors
    what the scanner is doing.
  * A matching advertisement resolves the wait and returns the device.
  * A diagnostic log fires for every Nintendo-ID advert when
    ``settings["show_gatt_dump"]`` is on (so the user can debug a
    firmware-variant prefix mismatch without source-level guessing).

We avoid pytest-asyncio (no new dep) by running each coroutine through
``asyncio.run``. ``BleakScanner`` is monkey-patched with a stub so no
real BT stack is required.
"""
import asyncio
import queue
import types

import pytest

import ble.connection as bc
from ble.constants import JOYCON_MANUFACTURER_ID, JOYCON_MANUFACTURER_PREFIX


class _StubScanner:
    """Stand-in for ``BleakScanner`` that captures the callback so tests
    can deterministically fire fake advertisements at it. Async-stops are
    no-ops; we don't simulate any real BT machinery."""

    def __init__(self, callback):
        self._callback = callback

    async def start(self):
        return None

    async def stop(self):
        return None


def _adv(manufacturer_id: int, data: bytes):
    """Build the minimum-viable advertisement object the scanner callback
    reads — only ``manufacturer_data`` is consulted."""
    return types.SimpleNamespace(manufacturer_data={manufacturer_id: data})


def _device(addr: str, name: str = "Joy-Con (R)"):
    """Stand-in for the bleak Device object — only ``address`` and
    ``name`` are read by the scanner callback."""
    return types.SimpleNamespace(address=addr, name=name)


# ── timeout path ────────────────────────────────────────────────────────


def test_scan_timeout_returns_none(monkeypatch):
    """If no matching advertisement arrives within ``timeout`` seconds,
    scan_device must return ``None`` instead of hanging.

    The original code's `while True: await event.wait()` could block
    forever if the JC2 didn't advertise the expected manufacturer prefix.
    We pin the timeout escape hatch."""
    monkeypatch.setattr(bc, "BleakScanner", _StubScanner)
    cq: queue.Queue = queue.Queue()

    result = asyncio.run(
        bc.scan_device(set(), "test controller", command_queue=cq, timeout=0.05)
    )

    assert result is None


def test_scan_timeout_pushes_user_visible_status(monkeypatch):
    """On timeout, the queue must contain both the initial 'Searching…'
    status AND the final red 'No Joy-Con found' status. Without these the
    UI would silently revert to its previous label and the user would
    have no signal that the scan completed."""
    monkeypatch.setattr(bc, "BleakScanner", _StubScanner)
    cq: queue.Queue = queue.Queue()

    asyncio.run(bc.scan_device(set(), "Joy-Con", command_queue=cq, timeout=0.05))

    events = []
    while not cq.empty():
        events.append(cq.get_nowait())

    assert len(events) == 2
    assert all(e["type"] == "status_update" for e in events)
    # First event is the searching prompt (blue).
    assert "Searching" in events[0]["data"]["text"]
    assert events[0]["data"]["color"] == "#3498db"
    # Final event is the failure message (red).
    assert "No Joy-Con found" in events[1]["data"]["text"]
    assert events[1]["data"]["color"] == "#e74c3c"


# ── happy path ──────────────────────────────────────────────────────────


def test_scan_returns_first_matching_device(monkeypatch):
    """A matching advert must resolve the wait and return the device.

    We swap in a scanner whose ``start`` schedules a fake advertisement
    on the running loop, so the awaiting coroutine sees the event fire
    promptly without any sleep races."""

    class _FiringScanner(_StubScanner):
        async def start(self):
            # Schedule the advert callback for the next event-loop tick
            # so the awaiting coroutine has time to enter wait_for first.
            loop = asyncio.get_event_loop()
            loop.call_later(0.001, self._callback,
                            _device("AA:BB:CC:DD:EE:FF"),
                            _adv(JOYCON_MANUFACTURER_ID,
                                 JOYCON_MANUFACTURER_PREFIX + b"\x05"))

    monkeypatch.setattr(bc, "BleakScanner", _FiringScanner)
    cq: queue.Queue = queue.Queue()

    result = asyncio.run(
        bc.scan_device(set(), "Joy-Con", command_queue=cq, timeout=2.0)
    )

    assert result is not None
    assert result.address == "AA:BB:CC:DD:EE:FF"

    # Status sequence: Searching → Connecting (no failure event).
    events = list(_drain(cq))
    assert len(events) == 2
    assert "Searching" in events[0]["data"]["text"]
    assert "Connecting" in events[1]["data"]["text"]
    assert events[1]["data"]["color"] == "#f39c12"


def test_scan_filters_out_used_addresses(monkeypatch):
    """An advert from an already-claimed address must be ignored. After
    the matching one is also fed in, the second wins."""

    class _MultiFiringScanner(_StubScanner):
        async def start(self):
            loop = asyncio.get_event_loop()
            # First fire: already-used address — must be ignored.
            loop.call_later(0.001, self._callback,
                            _device("11:11:11:11:11:11"),
                            _adv(JOYCON_MANUFACTURER_ID,
                                 JOYCON_MANUFACTURER_PREFIX + b"\x01"))
            # Second fire: fresh address — must resolve.
            loop.call_later(0.005, self._callback,
                            _device("22:22:22:22:22:22"),
                            _adv(JOYCON_MANUFACTURER_ID,
                                 JOYCON_MANUFACTURER_PREFIX + b"\x02"))

    monkeypatch.setattr(bc, "BleakScanner", _MultiFiringScanner)
    used = {"11:11:11:11:11:11"}

    result = asyncio.run(
        bc.scan_device(used, "Joy-Con", timeout=2.0)
    )

    assert result is not None
    assert result.address == "22:22:22:22:22:22"


def test_scan_filters_out_non_matching_prefix(monkeypatch):
    """A Nintendo-ID advert without the JC2 prefix must NOT resolve the
    scan. Used to defend against picking up a Switch console or other
    Nintendo BLE peripheral that happens to be advertising nearby."""

    class _WrongPrefixScanner(_StubScanner):
        async def start(self):
            loop = asyncio.get_event_loop()
            loop.call_later(0.001, self._callback,
                            _device("AA:BB:CC:DD:EE:FF"),
                            _adv(JOYCON_MANUFACTURER_ID,
                                 b"\x99\x99\x99\x99extra"))  # wrong prefix

    monkeypatch.setattr(bc, "BleakScanner", _WrongPrefixScanner)

    result = asyncio.run(
        bc.scan_device(set(), "Joy-Con", timeout=0.05)
    )

    # Wrong prefix → never resolves → timeout returns None.
    assert result is None


def test_scan_ignores_non_nintendo_adverts(monkeypatch):
    """Adverts without the Nintendo manufacturer ID must be silently
    skipped — there's no manufacturer_data entry for our key, so the
    callback's first guard (`if data is None: return`) fires."""

    class _UnrelatedScanner(_StubScanner):
        async def start(self):
            loop = asyncio.get_event_loop()
            # Different manufacturer ID — Nintendo's is 1363; pick anything else.
            loop.call_later(0.001, self._callback,
                            _device("AA:BB:CC:DD:EE:FF"),
                            _adv(0x004C, b"random apple advert"))

    monkeypatch.setattr(bc, "BleakScanner", _UnrelatedScanner)

    result = asyncio.run(
        bc.scan_device(set(), "Joy-Con", timeout=0.05)
    )

    assert result is None


# ── helpers ─────────────────────────────────────────────────────────────


def _drain(q: queue.Queue):
    """Iterator that yields all currently-queued items without blocking."""
    while True:
        try:
            yield q.get_nowait()
        except queue.Empty:
            return


# ── connection.scan_device's command_queue is optional ──────────────────


def test_scan_works_without_command_queue(monkeypatch):
    """``command_queue=None`` is the documented "library use" path —
    scan_device must not raise when no queue is provided."""
    monkeypatch.setattr(bc, "BleakScanner", _StubScanner)
    # No command_queue — must not raise even on timeout.
    result = asyncio.run(bc.scan_device(set(), timeout=0.05))
    assert result is None


# ── safety: the test of the test (catch a misconfigured stub) ──────────


def test_stub_scanner_does_not_use_real_bleak(monkeypatch):
    """Sanity check: confirm the monkeypatch path actually replaces the
    ``BleakScanner`` reference inside ble.connection. If this test ever
    fails because the import got refactored away, the other tests would
    silently be hitting the real BT stack."""
    monkeypatch.setattr(bc, "BleakScanner", _StubScanner)
    assert bc.BleakScanner is _StubScanner


# ── ensure our settings import is still resolved ────────────────────────


def test_diagnostic_logging_when_show_gatt_dump_on(monkeypatch):
    """When ``settings['show_gatt_dump']`` is True, every Nintendo-ID
    advert (matching or not) must be logged with its prefix-match status,
    so the user can diagnose firmware-variant prefix mismatches.

    The project's logger writes to ``sys.stderr`` via a ``StreamHandler``
    that captures ``sys.stderr`` at construction time, so neither
    ``caplog`` (root-logger only — ``propagate=False`` here) nor
    ``capsys`` (redirects ``sys.stderr`` after the handler is built)
    can see the records. We attach a list-collector handler directly to
    ``bc.log`` for the duration of the test instead, which is what
    pytest's caplog does internally for non-root loggers anyway.
    """
    import logging

    monkeypatch.setitem(bc.settings, "show_gatt_dump", True)

    captured: list[str] = []

    class _ListHandler(logging.Handler):
        def emit(self, record):
            captured.append(self.format(record))

    handler = _ListHandler(level=logging.INFO)
    handler.setFormatter(logging.Formatter("%(message)s"))
    bc.log.addHandler(handler)

    class _OneShotScanner(_StubScanner):
        async def start(self):
            loop = asyncio.get_event_loop()
            # Fire a Nintendo-ID advert with WRONG prefix — would normally
            # be silently filtered, but with diagnostics on it must log.
            loop.call_later(0.001, self._callback,
                            _device("AA:BB:CC:DD:EE:FF"),
                            _adv(JOYCON_MANUFACTURER_ID, b"\xff\xff\xff\xff"))

    monkeypatch.setattr(bc, "BleakScanner", _OneShotScanner)

    try:
        asyncio.run(bc.scan_device(set(), timeout=0.05))
    finally:
        bc.log.removeHandler(handler)

    assert any("Nintendo-ID advert" in m and "prefix-match=False" in m
               for m in captured), captured


# ── scan_device handles bleak callbacks where data may be None ──────────


def test_scan_callback_handles_missing_manufacturer_id(monkeypatch):
    """Some adverts have ``manufacturer_data`` but no entry for Nintendo's
    ID — ``.get(...)`` returns None. The callback must early-return on
    None instead of crashing on ``None.startswith(...)``."""

    class _NullDataScanner(_StubScanner):
        async def start(self):
            loop = asyncio.get_event_loop()
            # manufacturer_data dict, but our key is missing → .get() = None.
            loop.call_later(0.001, self._callback,
                            _device("AA:BB:CC:DD:EE:FF"),
                            types.SimpleNamespace(manufacturer_data={}))

    monkeypatch.setattr(bc, "BleakScanner", _NullDataScanner)
    # Just running without an exception is the test.
    result = asyncio.run(bc.scan_device(set(), timeout=0.05))
    assert result is None


# Quiet pytest's "unused import" complaint — pytest is needed for fixtures.
_ = pytest
