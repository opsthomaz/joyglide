# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for ``ble.connection`` connect / permission paths.

No real BT stack: ``BleakClient`` / ``BleakScanner`` are monkey-patched
with stubs. ``post_connect_setup`` is stubbed in the connect tests so
they exercise only the client construction + wiring, not the (already
tested) command sequence.
"""
import asyncio
import queue
import types

import pytest
from bleak.exc import BleakBluetoothNotAvailableError, BleakBluetoothNotAvailableReason

import ble.connection as bc


class _StubClient:
    """Records what ``BleakClient(...)`` was constructed with."""

    instances: list["_StubClient"] = []

    def __init__(self, address_or_device, disconnected_callback=None, **_kw):
        self.address_or_device = address_or_device
        self.disconnected_callback = disconnected_callback
        self.is_connected = False
        _StubClient.instances.append(self)

    async def connect(self, timeout=None):
        self.is_connected = True


@pytest.fixture
def stubbed(monkeypatch):
    _StubClient.instances = []
    monkeypatch.setattr(bc, "BleakClient", _StubClient)

    async def _noop_setup(*_a, **_kw):
        return None

    monkeypatch.setattr(bc, "post_connect_setup", _noop_setup)

    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(bc.asyncio, "sleep", _no_sleep)


def _player():
    return types.SimpleNamespace(address=None, name=None, clients=[],
                                 attach_joycon=lambda side: None)


class TestConnectUsesBLEDevice:
    def test_initial_connect_passes_device_object_not_address(self, stubbed, monkeypatch):
        """Passing the ``BLEDevice`` lets bleak's CoreBluetooth backend
        reuse the peripheral it already discovered. Passing the address
        string forces an extra scan inside ``connect()`` — slower, and a
        second point of failure right after the user's scan succeeded."""
        device = types.SimpleNamespace(address="AA:BB", name="Joy-Con (R)")
        monkeypatch.setitem(bc.settings, "devices", {"AA:BB": {"type": "right"}})

        async def handler(_client, _player):
            return None

        cq: queue.Queue = queue.Queue()
        asyncio.run(bc.connect_and_setup(device, _player(), handler, cq))

        (client,) = _StubClient.instances
        assert client.address_or_device is device


class TestScanPermissionDenied:
    def test_bluetooth_unavailable_returns_none_with_status(self, monkeypatch):
        """bleak ≥2 raises ``BleakBluetoothNotAvailableError`` when the
        user denied the macOS Bluetooth prompt or the radio is off. The
        scan must surface that instead of crashing the sync task."""

        class _DeniedScanner:
            def __init__(self, _cb):
                pass

            async def start(self):
                raise BleakBluetoothNotAvailableError(
                    "Bluetooth permission denied",
                    BleakBluetoothNotAvailableReason.DENIED_BY_USER)

            async def stop(self):
                return None

        monkeypatch.setattr(bc, "BleakScanner", _DeniedScanner)
        cq: queue.Queue = queue.Queue()

        result = asyncio.run(bc.scan_device(set(), "Joy-Con", command_queue=cq, timeout=0.05))

        assert result is None
        events = []
        while not cq.empty():
            events.append(cq.get_nowait())
        assert events[-1]["data"]["color"] == "#e74c3c"
        assert "Bluetooth" in events[-1]["data"]["text"]


class TestConnectDeadline:
    """``connect_and_setup`` must not hang forever if the BLE stack never
    answers. bleak 3.0.2's CoreBluetooth ``connect`` awaits a disconnect
    future with no timeout after its own timeout fires; if CoreBluetooth
    never calls back, the coroutine is stuck for good. A deadline of our
    own turns that into a ``TimeoutError`` the caller already handles
    (address released, Sync button re-enabled)."""

    def test_hung_connect_raises_timeout(self, monkeypatch):
        class _HangingClient(_StubClient):
            async def connect(self, timeout=None):
                await asyncio.Event().wait()   # never

        monkeypatch.setattr(bc, "BleakClient", _HangingClient)
        monkeypatch.setattr(bc, "CONNECT_DEADLINE_S", 0.05)
        device = types.SimpleNamespace(address="AA:BB", name="Joy-Con (R)")

        async def handler(_client, _player):
            return None

        with pytest.raises(TimeoutError):
            asyncio.run(bc.connect_and_setup(device, _player(), handler, queue.Queue()))
