# SPDX-License-Identifier: GPL-3.0-or-later
"""Singleton asyncio background loop + utilities for fire-and-forget work.

A single shared asyncio loop runs in a daemon thread for all "fire and
forget" coroutines triggered from non-async callers (tray menu items,
hotkey handlers, the main thread on boot). Earlier versions spawned a
fresh loop+thread per invocation, leaking both on every click.

  * ``run(coro)`` — schedule ``coro`` on the background loop. Lazy-starts
    the loop on first call. Returns a ``concurrent.futures.Future``.
  * ``save_settings_async()`` — convenience helper: persist settings on a
    daemon thread (not the bg asyncio loop) so menu callbacks never
    block on JSON file I/O.
"""
import asyncio
import threading
from threading import Thread

from user_preferences import save_settings, settings

_background_loop: asyncio.AbstractEventLoop | None = None
# Guards the lazy-init check-then-assign in ``_start_background_loop``.
# Two callers racing into the function (e.g. a tray-menu click on the
# pystray thread + the Tk Sync button on the AppKit main thread at
# first launch) would otherwise both pass the ``None`` check, both
# create separate loops, and both assign — the second wins, the first
# is orphaned with its scheduled tasks lost.
_start_lock = threading.Lock()


def _start_background_loop() -> None:
    """Lazily create the singleton bg event loop and run it on a daemon
    thread. Idempotent — second call is a no-op. Called automatically
    by ``run`` on first invocation."""
    global _background_loop
    with _start_lock:
        if _background_loop is not None:
            return
        loop = asyncio.new_event_loop()
        _background_loop = loop
        def _run() -> None:
            """Daemon-thread entry point — bind the new loop and run forever."""
            # This thread hosts every BLE notification callback and the
            # motion pump — the latency-critical path. On macOS, mark it
            # USER_INTERACTIVE so Apple Silicon keeps it on a P-core.
            from osio.boost import boost_current_thread_qos
            boost_current_thread_qos()
            asyncio.set_event_loop(loop)
            loop.run_forever()
        Thread(target=_run, daemon=True, name="joyglide-bg-loop").start()


def run(coro):
    """Schedule a coroutine on the background loop. Lazy-starts the loop."""
    if _background_loop is None:
        _start_background_loop()
    assert _background_loop is not None  # narrowing for type checkers — start above guarantees this
    return asyncio.run_coroutine_threadsafe(coro, _background_loop)


def save_settings_async() -> None:
    """Persist settings off the main thread.

    Tray menu callbacks fire on macOS's AppKit main thread, which is also
    tkinter's mainloop thread. A synchronous JSON write inside the callback
    blocks both — making the menu (and the whole UI) feel frozen until the
    write completes. Hand it to a daemon thread so the menu action returns
    to AppKit immediately.
    """
    Thread(target=save_settings, args=(settings,), daemon=True).start()
