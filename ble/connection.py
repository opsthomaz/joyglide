# SPDX-License-Identifier: GPL-3.0-or-later
"""BLE scan + connect + reconnect lifecycle.

Bridges the pure protocol primitives in ``ble.protocol`` with the app
layer (``settings``, ``command_queue``, ``Player``). This is where the
"how do we get from a fresh BLE scan to a fully-armed Joy-Con sending
input reports" orchestration lives.

The two top-level entry points:
  * ``scan_device`` — BLE advertising scan that filters by Nintendo's
    manufacturer ID prefix and resolves to a single device.
  * ``connect_and_setup`` + ``maintain_connection_loop`` — opens the
    GATT connection, runs the per-link initialization sequence (IMU
    enable, LEDs, optional vibration), wires the disconnect callback to
    an asyncio Event for backoff-based reconnects.

The per-link setup is non-trivial: the controller resets its feature
mask and IMU/mouse mode every time the BLE link drops, so reconnects
have to re-issue the same ``enable_mouse`` + ``set_leds`` sequence as
the initial connect — otherwise the cursor goes silently dead.
"""
import asyncio

from bleak import BleakClient, BleakScanner

from applog import get_logger
from ble.constants import JOYCON_MANUFACTURER_ID, JOYCON_MANUFACTURER_PREFIX
from ble.protocol import (
    cancel_bluetooth_advertising,
    dump_gatt_profile,
    enable_mouse,
    play_vibration_preset,
    set_leds,
)
from user_preferences import settings

log = get_logger(__name__)


async def scan_device(used_addresses: set, prompt: str = "controller", *,
                       command_queue=None, timeout: float = 30.0):
    """Scan for a Joy-Con 2 advertising the Nintendo manufacturer prefix.

    Returns the first matching device that hasn't already been claimed in
    this session (``used_addresses`` is shared across calls so a multi-
    controller setup flow doesn't re-pair the same controller twice).
    Returns ``None`` on timeout (no device found in ``timeout`` seconds).

    Pushes ``status_update`` events to ``command_queue`` if provided, so
    the UI mirrors what the scanner is doing in real time. Without these,
    the user clicks Sync and sees nothing change for up to ``timeout``
    seconds — the original "looks like syncing but isn't" symptom.

    Diagnostic aid: when ``settings["show_gatt_dump"]`` is on, logs every
    Nintendo-manufacturer-ID advert seen, with prefix-match status. Useful
    when a JC2 firmware variant ships a different manufacturer-data prefix
    and the default filter rejects it silently.
    """
    log.info(f"🔍 Searching for your {prompt} (press sync)...")
    if command_queue is not None:
        command_queue.put({"type": "status_update", "data": {
            "text": f"🔍 Searching for {prompt}… (hold the sync button)",
            "color": "#3498db",
        }})

    found_devices: list = []
    device_event = asyncio.Event()
    diagnostic = settings.get("show_gatt_dump", False)

    def callback(device, adv):
        """BleakScanner callback — fires per BLE advertisement. Filters
        by Nintendo manufacturer ID + prefix and rejects already-claimed
        addresses, then signals the outer coroutine via the asyncio Event."""
        if device.address in used_addresses:
            return
        data = adv.manufacturer_data.get(JOYCON_MANUFACTURER_ID)
        if data is None:
            return
        if diagnostic:
            # Log every Nintendo-ID advert when diagnostics are on. Lets the
            # user see whether their JC2 advertises the expected prefix —
            # firmware variants sometimes ship different bytes here.
            log.info(f"  📡 Nintendo-ID advert {device.address} data={data.hex()} "
                     f"prefix-match={data.startswith(JOYCON_MANUFACTURER_PREFIX)}")
        if (data.startswith(JOYCON_MANUFACTURER_PREFIX)
                and not any(d.address == device.address for d in found_devices)):
            found_devices.append(device)
            log.info(f"  Found {device.name or 'Unknown'} ({device.address})")
            device_event.set()

    scanner = BleakScanner(callback)
    await scanner.start()

    try:
        # asyncio.wait_for raises TimeoutError after `timeout` seconds. The
        # original code did `while True: await device_event.wait()`, which
        # could hang forever if the JC2 never advertised the expected
        # prefix (firmware variant, BT permission missing, JC2 already
        # paired to a Switch and not in sync mode, etc.).
        await asyncio.wait_for(device_event.wait(), timeout=timeout)
    except TimeoutError:
        log.warning(f"⏱️ Scan timeout after {timeout:.0f}s — no Joy-Con found")
        if command_queue is not None:
            command_queue.put({"type": "status_update", "data": {
                "text": "❌ No Joy-Con found. Hold the sync button and click again.",
                "color": "#e74c3c",
            }})
        return None
    finally:
        await scanner.stop()

    selected_device = found_devices[0]
    log.info(f"🎮 Selected {selected_device.name or 'Unknown'} ({selected_device.address})")
    if command_queue is not None:
        command_queue.put({"type": "status_update", "data": {
            "text": f"🔗 Connecting to {selected_device.name or 'Joy-Con'}…",
            "color": "#f39c12",
        }})
    return selected_device


async def post_connect_setup(client, player, *, vibrate: bool) -> None:
    """Send the per-connection commands the controller needs after every BLE link.

    Called from both ``connect_and_setup`` (initial connect) and
    ``maintain_connection_loop`` (reconnect). The commands here are NOT
    persistent across BLE reconnects — IMU/mouse mode is reset by the
    controller firmware whenever the link drops, so we have to re-send
    them or the cursor will silently stop responding after a reconnect.
    """
    # Windows-only: drop LL connection interval from 60ms → 15ms via the
    # WinRT BluetoothLEPreferredConnectionParameters.ThroughputOptimized
    # preset (~67Hz). No-op on macOS.
    from osio.boost import request_throughput_optimized
    await request_throughput_optimized(client)
    if settings.get("show_gatt_dump", False):
        await dump_gatt_profile(client)
    # Cancel any in-flight BLE advertising state. Required especially on
    # reconnect-via-sync-button: the JC2 stays in advertising mode and
    # its LED cycle inherits over our set_leds writes (ndeadly's docs
    # explicitly note this firmware quirk). Empty-payload command, safe
    # to call on initial connect too — no-op when not advertising.
    await cancel_bluetooth_advertising(client)
    await set_leds(client, player.number)
    if vibrate and settings.get("vibration_on_connect", True):
        await asyncio.sleep(0.4)
        # 0x03 is documented (per ndeadly) as the "Connection" sample —
        # a soft click-click. 0x04 is a beep alarm meant for low-battery
        # warnings and barely registers as vibration.
        await play_vibration_preset(client, 0x03)
        await asyncio.sleep(0.4)
    await enable_mouse(client)


async def connect_and_setup(device, player, handler_func, command_queue,
                             disconnect_cb=None, *handler_args):
    """Open a GATT connection, run the per-link setup, attach the input
    notification handler. Returns the connected ``BleakClient``.
    """
    # ``disconnected_callback`` accepts ``None`` — no need for the
    # conditional. The previous ``client._device = device`` line set a
    # private bleak attribute that nothing in this codebase reads; it's
    # been removed to avoid future collisions with bleak internals.
    client = BleakClient(device.address, disconnected_callback=disconnect_cb)
    await client.connect(timeout=5.0)
    player.address = device.address
    player.name    = device.name or "Joy-Con"
    await asyncio.sleep(0.5)
    await post_connect_setup(client, player, vibrate=True)
    if device.address not in settings["devices"]:
        command_queue.put({"type": "new_joy_window", "data": {"controller_id": device.address, "player": player}})
    else:
        player.attach_joycon(settings["devices"][device.address]["type"])
    await handler_func(client, player, *handler_args)
    player.clients.append(client)
    log.info(f"✅ Connected to {device.address}")
    return client


async def maintain_connection_loop(client, device, player, handler_func,
                                    disconnect_event, command_queue,
                                    disconnect_cb=None, *handler_args) -> None:
    """Wait on the BLE-stack disconnect event and reconnect with exponential
    backoff. Re-runs the per-link setup on every reconnect because the
    controller's feature mask and IMU/mouse state reset on link drop.

    Two macOS-specific quirks the loop has to defend against:

      * After macOS BT is toggled off, the existing ``BleakClient``
        instance is poisoned — its internal CBCentralManager reference
        is no longer valid and ``client.connect()`` fails immediately
        with "Local device is powered off". A *fresh* ``BleakClient``
        is needed; we recreate it on every retry.
      * ``client.disconnect()`` can hang indefinitely if the BT stack
        is in a bad state. We bound it with a timeout so the loop
        never gets stuck in the except branch.
    """
    retry_delay = 1.0
    while True:
        try:
            # Wait for the BLE stack to fire the disconnected_callback — no polling.
            await disconnect_event.wait()
            disconnect_event.clear()

            command_queue.put({"type": "status_update", "data": {"connected": False, "text": f"Reconnecting {device.name or 'Joy-Con'}...", "color": "#f39c12"}})
            log.info(f"🔄 Reconnect attempt (retry_delay={retry_delay:.1f}s)…")

            # Recreate the client. Reusing a BleakClient across BT-stack
            # cycles on macOS leaves it in a state where connect()
            # fails fast forever (CBInternalErrorDomain Code=32 "Local
            # device is powered off"). We rebuild and re-register the
            # caller-provided disconnect callback.
            client = BleakClient(device.address, disconnected_callback=disconnect_cb)
            await client.connect(timeout=5.0)

            # Settle delay — same as the initial connect_and_setup has.
            # Without this, post_connect_setup's writes (set_leds,
            # enable_mouse) race the JC2's post-link initialisation
            # and silently fail: the BLE layer accepts the writes but
            # the firmware drops them. Symptom: reconnected cleanly
            # at GATT level, but LEDs keep flashing the pairing
            # pattern and mouse mode never re-enables.
            await asyncio.sleep(0.5)

            # Reset engine state cleanly before continuing
            if player.gamepad:
                player.gamepad.reset_state()

            # CRITICAL: re-send the per-connection setup. The controller
            # firmware resets IMU / mouse mode on every BLE link drop, so
            # without this the reconnect would succeed at the BLE layer
            # but the cursor would silently stop responding.
            await post_connect_setup(client, player, vibrate=False)
            await handler_func(client, player, *handler_args)
            # Update player.clients to point at the new client.
            player.clients = [client]
            log.info(f"🔄 Reconnected to {device.address}")
            retry_delay = 1.0 # Reset backoff
            command_queue.put({"type": "status_update", "data": {"connected": True, "text": f"Connected: {device.name or 'Joy-Con'}", "color": "#2ecc71"}})

        except Exception as e:
            # Empty-message BleakError is common when macOS CoreBluetooth
            # cancels mid-call; log the type so the cascade is diagnosable.
            log.warning(f"⚠️ Connection lost or error: {type(e).__name__}: {e!r}")
            command_queue.put({"type": "status_update", "data": {"connected": False, "text": f"Connection Lost. Retrying in {int(retry_delay)}s...", "color": "#e74c3c"}})
            # Bounded disconnect — when the BT stack is off,
            # client.disconnect() can hang and block the retry loop.
            try:
                if client.is_connected:
                    await asyncio.wait_for(client.disconnect(), timeout=2.0)
            except TimeoutError as cleanup_err:
                # Timeout is the expected failure mode when the BT stack is
                # off — not a real problem, keep it at debug.
                log.debug(f"disconnect cleanup timed out: {cleanup_err}")
            except Exception as cleanup_err:
                # Anything else is unexpected (bleak internal error, etc.)
                # — surface it at warning so it's visible in logs.
                log.warning(f"disconnect cleanup failed: "
                            f"{type(cleanup_err).__name__}: {cleanup_err}")

            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 1.5, 10.0) # Exponential backoff up to 10s
            disconnect_event.set()  # re-trigger the reconnect attempt
