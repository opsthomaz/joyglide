# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-controller state holder.

A ``Player`` is the lifecycle object for one connected Joy-Con 2:

  * Holds the BLE client(s) and the asyncio reconnection task.
  * Owns a ``JoyCon`` motion engine instance (``self.gamepad``).
  * Tracks identity: address (BD_ADDR), name, side (left/right), and the
    in-app player number (which drives the LED pattern).

The lifecycle is:

  1. ``__init__`` creates an empty Player (no JoyCon yet).
  2. ``connect_and_setup`` (in ``main.py``) populates ``address``/``name``
     and either calls ``attach_joycon(side)`` immediately (if the controller
     was previously paired and we already know its side) or pushes a
     ``new_joy_window`` event so the user can pick the side.
  3. ``maintain_connection_loop`` keeps a task alive that reconnects on
     drop. The task reference is stored in ``self.task`` so we can cancel
     it during teardown.
  4. ``switch_side`` lets the user flip left↔right at runtime without
     disconnecting (re-maps button bitmasks, keeps the pump running).
  5. ``disconnect`` cancels the reconnect task first (to avoid a race
     where the disconnect callback would trigger a reconnect mid-teardown),
     then closes the BLE client, then stops the pump task.
"""
from contextlib import suppress
from typing import TYPE_CHECKING

from joycon import JoyCon

if TYPE_CHECKING:
    import asyncio


class Player:
    """Lifecycle holder for one connected Joy-Con 2.

    Aggregates the BLE client(s), the asyncio reconnection task, and the
    motion engine (``self.gamepad``) under a single object that the UI
    layer can list and the BLE layer can mutate. Construction is cheap
    (no I/O); the actual connection is wired in
    ``ble.connection.connect_and_setup`` and the motion engine is bound
    via ``attach_joycon`` once the user picks a side.
    """

    def __init__(self, number: int, controller_type: str, side: str | None = None, task: "asyncio.Task | None" = None) -> None:
        """Create an empty Player slot. ``number`` is the 1-based player
        index that drives the LED pattern; ``controller_type`` reserves
        for future controller variants beyond a single Joy-Con. ``side``
        and ``task`` are populated by the BLE wiring layer once connected."""
        self.number = number              # 1-based slot, drives the LED pattern
        self.type = controller_type       # "SINGLE_JOYCON" today; reserved for future combos
        self.side = side                  # "left" or "right" once attached
        self.clients: list = []           # bleak BleakClient(s) — one for now
        self.task = task                  # asyncio task running maintain_connection_loop
        # Populated in main.connect_and_setup so the UI can identify each row.
        self.address: str | None = None
        self.name: str | None    = None
        self.gamepad: JoyCon | None = None

    def __str__(self) -> str:
        return f"Player {self.number} ({self.side or '?'}) @ {self.address}"

    def attach_joycon(self, side: str) -> None:
        """Bind a fresh ``JoyCon`` motion engine to this player.

        Called once per controller after the side has been identified.
        The motion engine then takes over button/stick/sensor parsing
        and drives the cursor.
        """
        self.side = side
        self.gamepad = JoyCon(side=side)

    def switch_side(self, side: str) -> None:
        """Change left↔right at runtime without disconnecting.

        Delegates to ``JoyCon.set_side``, which swaps the button bitmask in
        place and resets ``last_data`` so the next packet's button diff is
        clean. The BLE connection and the pump task stay alive.
        """
        self.side = side
        if self.gamepad:
            self.gamepad.set_side(side)

    def set_paused(self, paused: bool) -> None:
        """Pause or resume cursor input from this controller.

        Toggled by the global hotkey (Ctrl+Alt+M / ⌃⌥M). The BLE pipeline
        keeps running so resume is instant — only output is gated.
        """
        if self.gamepad:
            self.gamepad.paused = paused

    async def disconnect(self) -> None:
        """Tear down the connection cleanly. Order matters."""
        # Cancel the maintain-connection task FIRST so the disconnect we're
        # about to issue doesn't fire its disconnect callback and re-trigger
        # a reconnect attempt mid-shutdown.
        if self.task and not self.task.done():
            self.task.cancel()
        for client in self.clients:
            # Best effort — even if disconnect fails, we still want to stop
            # the pump and clear our refs.
            with suppress(Exception):
                if client.is_connected:
                    await client.disconnect()
        self.clients.clear()
        # Stop the motion pump so nothing keeps trying to inject events.
        if self.gamepad and self.gamepad._pump_task and not self.gamepad._pump_task.done():
            self.gamepad._pump_task.cancel()
