# SPDX-License-Identifier: GPL-3.0-or-later
"""Joyglide — application entry point.

Wires the major subsystems together:

  * **BLE scan / connect / reconnect** via ``ble.connection`` (which
    builds on ``ble.protocol`` for the wire-level commands).
  * **Per-controller state** in ``Player``. Each player owns its own
    asyncio loop on its own daemon thread, so multiple controllers can
    run concurrently without contention.
  * **Cross-platform boot hooks** (priority boost, anti-throttle, Windows
    BLE rate negotiation) via ``platform_setup``.
  * **Tray icon and dashboard UI** (``tray.create_icon`` builds the
    pystray menu, ``ui.JoyglideUI`` builds the customtkinter window).
    The two threads communicate through a ``command_queue`` (BLE threads
    → Tk thread, never the reverse).
  * **Background asyncio loop** for fire-and-forget coroutines from
    non-async contexts (tray callbacks, hotkey, boot) — see ``bg_loop``.
  * **Global pause hotkey** registered via ``hotkey.install_pause_hotkey``.

Settings are persisted to JSON via ``user_preferences``; profile/sensitivity
changes take effect immediately because the BLE threads read the same
``settings`` dict on every packet.
"""
import asyncio
import os
import queue
import subprocess
import sys
from threading import Lock

from applog import get_logger
from ble.connection import (
    connect_and_setup as ble_connect_and_setup,
    maintain_connection_loop as ble_maintain_connection_loop,
    scan_device as ble_scan_device,
)
from ble.constants import INPUT_REPORT_JCL_UUID, INPUT_REPORT_JCR_UUID, INPUT_REPORT_UUID
from ble.protocol import play_vibration_preset, set_leds
from bg_loop import run as bg_run
from player import Player
from solo_logic import handle_side_specific_notification, handle_single_notification
from tray import create_icon
from user_preferences import save_settings, settings

log = get_logger(__name__)


# Addresses we've already paired in this session — prevents double-handling
# of the same advertisement during a multi-player setup flow.
used_addresses: set = set()

# Cross-thread channel: BLE / asyncio threads enqueue UI updates here, the
# Tk mainloop drains it on a 100ms timer (see ui.py:process_queue).
command_queue: queue.Queue = queue.Queue()

# All currently connected players. One Player per controller.
players: list[Player] = []

# Serialises access to `players` from the multiple threads that mutate it
# (each tray_connect_new_controller runs on its own daemon thread, plus
# disconnect_player runs on the player's own asyncio loop, plus the hotkey
# thread iterates the list to broadcast pause). Held only briefly around
# the read-modify-write on `players` itself, never around await/I/O.
_players_lock = Lock()


async def handle_single_joycon(client, player: "Player") -> None:
    """Subscribe to the controller's input-report 0x05 GATT notifications
    and route each packet through ``solo_logic.handle_single_notification``
    (which fans out to the parser/engine on the player's gamepad).

    Idempotent: bleak's ``start_notify`` raises ``BleakError(
    "Characteristic notifications already started")`` when called twice.
    On macOS the disconnect callback occasionally fires spuriously
    (without a real link drop), and the reconnect path re-runs this
    handler — without the swallow, the maintain loop cascades into
    repeated retries. If the original subscription is still alive,
    a no-op here is the correct behaviour; if it isn't, the next real
    disconnect will trigger a clean reconnect.

    Also subscribes to the side-specific report (0x07 JC-L / 0x08 JC-R)
    when ``player.side`` is already known, so we get the firmware's own
    battery-level estimate. For new devices where side is picked via the
    modal, ``player.attach_joycon`` will trigger the second subscribe
    later.
    """
    def cb(sender, data):
        """bleak start_notify callback — fires per BLE notification."""
        handle_single_notification(sender, data, player.gamepad)
    try:
        await client.start_notify(INPUT_REPORT_UUID, cb)
    except Exception as e:
        # bleak doesn't expose a typed "already started" error class
        # (and the message string is the only signal). Match by content
        # to avoid swallowing genuine subscription failures.
        if "already started" in str(e).lower():
            log.debug(f"start_notify already active on {client.address} — skipping")
        else:
            raise

    # NOTE on side-specific input reports (0x07 JC-L / 0x08 JC-R):
    # we deliberately do NOT subscribe here, even though it would give
    # us the JC2 firmware's own 4-bit battery level (0..9). Hardware
    # verification on a JC2 (R) over BLE on macOS shows that
    # subscribing to a second *input-report* notify characteristic on
    # the same peripheral kills the first subscription's stream —
    # input report 0x05 stops emitting after the second start_notify
    # completes. Possibly a CoreBluetooth constraint, possibly a JC2
    # firmware limit. Either way, every working driver in research/
    # (coffincolors, TropicalCyclone, JoyConPlusPlus) subscribes to
    # ONE input report, not both.
    #
    # The constraint applies specifically to parallel *input-report*
    # subscribes; it does NOT apply to command-response notify
    # channels. The BASIC channel `c765a961-…` was tested to coexist
    # with input report 0x05 cleanly (see docs/ARCHITECTURE.md
    # "Tier-0 hardware-verified command responses").
    #
    # The 4-bit firmware-computed battery level is exclusive to
    # side-specific input reports — no command returns it. So
    # battery percentage falls back to parser.battery's voltage
    # approximation (linear 3300 mV → 0 %, 4200 mV → 100 %), which
    # remains the working path on macOS without major refactor. The
    # parser.power_info module + INPUT_REPORT_JC{L,R}_UUID constants
    # + subscribe_side_specific() helper below are kept as
    # scaffolding for future use via command-based polling
    # (cmd 0x0B/…) which doesn't compete with input-notify.


async def subscribe_side_specific(client, player: "Player") -> None:
    """Subscribe to the side-specific input report (0x07 JC-L / 0x08
    JC-R) for the firmware's Power Info bitfield.

    **Currently unused** — see the NOTE in handle_single_joycon for
    why. Kept here so a future command-poll-or-toggle approach can
    reuse the wiring.
    """
    if player.side == "right":
        side_uuid = INPUT_REPORT_JCR_UUID
    elif player.side == "left":
        side_uuid = INPUT_REPORT_JCL_UUID
    else:
        return  # unknown side — nothing to subscribe to

    def side_cb(sender, data):
        handle_side_specific_notification(sender, data, player.gamepad)

    try:
        await client.start_notify(side_uuid, side_cb)
        log.info(f"📊 Side-specific report subscribed ({player.side}) — "
                 f"firmware battery level active.")
    except Exception as e:
        if "already started" in str(e).lower():
            log.debug(f"side-specific notify already active on {client.address}")
        else:
            log.warning(
                f"⚠️ Side-specific subscribe failed ({type(e).__name__}: {e}). "
                f"Battery percentage falls back to voltage approximation."
            )


async def setup_player(number: int) -> "Player | None":
    """Scan for a fresh Joy-Con, open the GATT connection, attach the input
    handler, and start the reconnect-watchdog task.

    Returns ``None`` on either scan timeout or connect failure, with a
    status pushed to the UI describing what went wrong. On connect
    failure the address is removed from ``used_addresses`` so the user
    can retry the same controller without restarting the app — without
    that, the scanner would silently filter the address out next time
    and the retry would no-op.
    """
    log.info(f"🎮 Setting up Player {number}")
    device = await ble_scan_device(used_addresses, f"Player {number} Joy-Con",
                                    command_queue=command_queue)
    if not device:
        return None
    used_addresses.add(device.address)

    player = Player(number, "SINGLE_JOYCON")

    # Wire BLE-stack disconnect callback → asyncio Event, replacing 1Hz polling.
    disconnect_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    # Default-args bind `loop` and `disconnect_event` at definition time
    # (vs. late-binding from the enclosing scope). Belt-and-suspenders
    # since the function returns within this iteration anyway, but it
    # also silences ruff's B023 warning.
    def _on_disconnect(_client, loop=loop, ev=disconnect_event):
        """BLE-stack disconnect callback — fires from the bleak
        thread; hops to the player's loop to set the wake event."""
        loop.call_soon_threadsafe(ev.set)

    try:
        client = await ble_connect_and_setup(device, player, handle_single_joycon,
                                              command_queue, _on_disconnect)
    except Exception as e:
        # Release the address so retry works without restarting the app.
        # The scanner filters used_addresses; a sticky entry would silently
        # block any subsequent attempt on the same controller.
        used_addresses.discard(device.address)
        log.warning(f"❌ Connect failed for {device.address}: {type(e).__name__}: {e}")
        command_queue.put({"type": "status_update", "data": {
            "text": f"❌ Connection failed ({type(e).__name__}). Try again.",
            "color": "#e74c3c",
        }})
        return None

    task = asyncio.create_task(ble_maintain_connection_loop(
        client, device, player, handle_single_joycon, disconnect_event,
        command_queue, _on_disconnect))
    player.task = task
    command_queue.put({"type": "status_update", "data": {
        "text": f"✅ Connected: {player.name or 'Joy-Con'}",
        "color": "#2ecc71",
    }})
    return player


async def add_player(number: int) -> "int | bool":
    """High-level "add a new controller" coroutine. Runs the BLE scan +
    connect flow via ``setup_player``, registers the result in the global
    ``players`` list under the lock, and pushes a ``player_list_changed``
    event so the UI dashboard refreshes. Returns the player number on
    success or ``False`` on failure.

    Brackets the whole flow with ``sync_state`` events so the dashboard
    can disable the Sync button while a scan/connect is in flight (and
    re-enable it on result). Without this the user could click Sync
    repeatedly and spawn parallel scans that share ``used_addresses``,
    where the second can never see what the first found.
    """
    command_queue.put({"type": "sync_state", "data": {"active": True}})
    try:
        player = await setup_player(number)
    finally:
        command_queue.put({"type": "sync_state", "data": {"active": False}})
    if not player:
        log.error("❌ Setup failed.")
        return False
    with _players_lock:
        players.append(player)
    command_queue.put({"type": "player_list_changed"})
    return number


async def disconnect_player(player: "Player") -> None:
    """Tear down the BLE link for ``player``, remove it from the global
    list under the lock, renumber the remaining players so LED slots
    stay sequential, and re-issue the LED command on each surviving
    controller from its own asyncio loop."""
    try:
        await player.disconnect()
    finally:
        with _players_lock:
            if player in players:
                players.remove(player)
            # Renumber remaining players so the LED pattern stays sequential
            # without gaps if the user disconnects a middle slot.
            for i, p in enumerate(players, start=1):
                p.number = i
            remaining = players.copy()
        # Re-send LED commands OUTSIDE the lock so we don't hold it across
        # I/O. We need to schedule each set_leds on the player's own loop
        # because clients live on per-player loops (each maintain_connection_loop
        # owns its loop).
        for p in remaining:
            if p.task and not p.task.done() and p.clients:
                _schedule_set_leds(p)
        command_queue.put({"type": "player_list_changed"})


def _schedule_set_leds(player: "Player") -> None:
    """Re-send the player-LED pattern on the player's own asyncio loop.

    Used after a disconnect renumbers the remaining players so the LEDs
    match the new player numbers. Safe to call from any thread; uses
    ``run_coroutine_threadsafe`` to hop onto the right loop.
    """
    if player.task is None:
        return
    try:
        loop = player.task.get_loop()
    except Exception:
        return
    async def _go():
        """Inner coroutine — re-issue set_leds for every connected client
        on the player's own loop. Errors are logged, never raised."""
        for c in player.clients:
            if c.is_connected:
                try:
                    await set_leds(c, player.number)
                except Exception as e:
                    log.warning(f"⚠️ set_leds failed for player {player.number}: {e}")
    asyncio.run_coroutine_threadsafe(_go(), loop)


def tray_disconnect_player(player: "Player") -> None:
    """UI-thread shim for ``disconnect_player``: schedules the async
    teardown on the player's own loop via ``run_coroutine_threadsafe``.
    Safe to call from the Tk thread (the dashboard's Disconnect button)."""
    if player.task is None:
        return
    try:
        loop = player.task.get_loop()
    except Exception:
        return
    asyncio.run_coroutine_threadsafe(disconnect_player(player), loop)


def tray_switch_side(player: "Player") -> None:
    """Flip ``player.side`` left↔right at runtime without disconnecting.
    Persists the new side in settings so the next reconnect picks it up
    automatically. Refreshes the dashboard via the queue."""
    new_side = "right" if player.side == "left" else "left"
    player.switch_side(new_side)
    if player.address and player.address in settings["devices"]:
        settings["devices"][player.address]["type"] = new_side
        save_settings(settings)
    command_queue.put({"type": "player_list_changed"})


# ── Global pause state (toggled by ⌃⌥M hotkey) ─────────────────────────────
_paused = False

def toggle_global_pause() -> None:
    """Hotkey-thread callback — flip pause on every connected player.

    Runs on the OS hotkey listener thread, NOT the asyncio loops.
    Snapshot the players list before iterating so a concurrent connect/
    disconnect doesn't raise ``RuntimeError`` ("list changed size").
    """
    global _paused
    _paused = not _paused
    # Snapshot the players list before iterating — this runs in the hotkey
    # thread, while connect/disconnect can mutate `players` from BLE threads.
    # Without the snapshot, `for p in players` could raise RuntimeError
    # ("list changed size during iteration") under concurrent connection events.
    for p in players.copy():
        p.set_paused(_paused)
    command_queue.put({"type": "pause_state", "data": {"paused": _paused}})
    log.info(f"{'⏸️  Paused' if _paused else '▶️  Resumed'} cursor input.")


async def play_vibration_for_all(preset_id: int) -> None:
    """Play ``preset_id`` on every connected controller, with verbose logs.

    Used by the tray's "Say hi" and "DEBUG → Test Vibration" actions.
    """
    # Snapshot under the lock so we don't iterate `players` while a
    # concurrent disconnect mutates it.
    with _players_lock:
        snapshot = players.copy()

    if not snapshot:
        log.warning("⚠️ No connected controller — vibration not sent.")
        return

    for player in snapshot:
        if not player.clients:
            log.warning(f"⚠️ Player {player.number} has no clients.")
            continue
        for client in player.clients:
            label = f"P{player.number} ({player.side or '?'}) {player.address}"
            connected = getattr(client, "is_connected", "?")
            log.info(f"🔔 vibration preset 0x{preset_id:02x} → {label} (connected={connected})")
            try:
                await play_vibration_preset(client, preset_id)
                log.info("   ✓ write succeeded")
            except Exception as e:
                log.warning(f"   ✗ write failed: {e}")


async def emit_sound() -> None:
    """'Say hi' — play a clearly-felt buzz on every connected controller."""
    # 0x05 is a stronger click than 0x03; 0x01 is the longest buzz.
    # 0x05 fires quick and feels like a real haptic — best for "say hi".
    await play_vibration_for_all(0x05)


# Set when an add_player is in flight. The UI button-disable handles user
# clicks; this guard catches programmatic re-entry (e.g. boot-time
# start_with_sync racing with a tray menu click before the UI hooks the
# sync_state event). Cleared by the future's done-callback so it survives
# any exception inside add_player. The lock makes the check-then-set
# atomic across threads — without it, the tray-menu callback thread and
# the Tk Sync button could both pass the ``False`` check, both flip to
# ``True``, and both kick a scan.
_sync_in_progress = False
_sync_lock = Lock()


def tray_connect_new_controller() -> None:
    """Tray-callback shim — schedule ``add_player`` on the bg loop with the
    next available player slot number. Holds ``_players_lock`` only long
    enough to compute ``next_number`` so concurrent clicks don't race
    onto the same slot. Re-entrant calls while a scan is in flight are
    dropped with a warning (the UI button-disable is the primary defense
    against double-click; this is belt-and-suspenders for non-UI callers
    like the boot path)."""
    global _sync_in_progress
    with _sync_lock:
        if _sync_in_progress:
            log.info("⚠️ Sync already in progress — ignoring duplicate request")
            return
        _sync_in_progress = True
    # Compute next_number under the lock to prevent two concurrent clicks
    # from both reading len(players)=N and assigning the same player number.
    with _players_lock:
        next_number = len(players) + 1
    fut = bg_run(add_player(next_number))

    def _done(_f):
        """Future done-callback — clears the in-progress flag whether
        add_player succeeded, returned False, or raised."""
        global _sync_in_progress
        with _sync_lock:
            _sync_in_progress = False
    fut.add_done_callback(_done)


def tray_emit_sound() -> None:
    """Tray-callback shim — schedule ``emit_sound`` on the bg loop."""
    bg_run(emit_sound())


from ui import JoyglideUI  # noqa: E402  — late import: avoids loading customtkinter before logging is configured

app_ui = JoyglideUI(
    command_queue,
    tray_connect_new_controller,
    players_ref=players,
    disconnect_fn=tray_disconnect_player,
    switch_side_fn=tray_switch_side,
)

if settings.get("ignore_opening_window"):
    app_ui.withdraw()

if __name__ == "__main__":
    # 1. Boost process priority + anti-throttle — macOS uses NSActivity,
    #    Windows uses SetPriorityClass + SetThreadExecutionState.
    from osio.boost import boost_process_priority
    boost_process_priority()

    # 2. Global hotkey Ctrl+Alt+M / ⌃⌥M to pause/resume cursor.
    from osio.hotkey import install_pause_hotkey
    install_pause_hotkey(toggle_global_pause)

    # 3. Check for macOS Accessibility Permissions and Boot
    from osio.mouse import check_accessibility, request_accessibility
    if not check_accessibility():
        log.warning("⚠️ macOS Accessibility Permission missing!")

        # macOS-only: wipe any stale TCC entry from a previous build's
        # PyInstaller ad-hoc signature BEFORE prompting. Without this,
        # AXIsProcessTrustedWithOptions often doesn't fire the system
        # prompt — it sees an existing entry for this bundle ID (left
        # over from an earlier build's signature) and treats the
        # registration as a no-op. The user is then stuck with a
        # toggle in System Settings that LOOKS enabled but is tied to
        # a signature the current process doesn't match. Resetting
        # first gives a clean prompt aligned with the running
        # signature. No-op on first launch (nothing to reset) and on
        # non-Darwin (tccutil doesn't exist there).
        if sys.platform == "darwin":
            subprocess.run(
                ["tccutil", "reset", "Accessibility", "com.opsthomaz.joyglide"],
                capture_output=True, check=False,
            )

        # Trigger the system prompt and register the app in the TCC list.
        # Without this the app never appears in System Settings →
        # Accessibility, so the user has nothing to toggle. No-op when
        # already trusted; on Windows this is a no-op too.
        request_accessibility()
        app_ui.deiconify()  # Force UI to show
        command_queue.put({"type": "show_accessibility_warning"})
    else:
        log.info("✅ Accessibility Permission granted.")
        if settings.get("start_with_sync"):
            tray_connect_new_controller()

    icon = create_icon(
        command_queue=command_queue,
        on_sync_new=tray_connect_new_controller,
        on_emit_sound=tray_emit_sound,
        on_play_vibration=lambda pid: bg_run(play_vibration_for_all(pid)),
        on_quit=lambda: os._exit(0),
    )
    icon.run_detached()

    app_ui.mainloop()
