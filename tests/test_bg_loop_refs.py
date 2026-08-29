# SPDX-License-Identifier: GPL-3.0-or-later
"""``bg_loop.run`` must hold a strong reference to every scheduled task
until it finishes.

asyncio only keeps *weak* references to tasks. A task blocked on a
future that is itself only reachable from the task's own frame (a
reference cycle) has no external owner and gets garbage-collected —
observed on 2026-08-29: bleak's CoreBluetooth connect hit its timeout,
then awaited a disconnect future with no timeout; the ``add_player``
task was destroyed mid-await ("Task was destroyed but it is pending!"),
its done-callback never ran, and ``_sync_in_progress`` stayed True
forever.
"""
import asyncio
import contextlib
import time

import bg_loop


def test_run_keeps_future_until_done():
    gate = asyncio.Event()

    async def _blocked():
        await gate.wait()
        return "done"

    fut = bg_loop.run(_blocked())
    assert fut in bg_loop._pending

    loop = bg_loop._background_loop
    assert loop is not None
    loop.call_soon_threadsafe(gate.set)
    assert fut.result(timeout=5) == "done"

    deadline = time.monotonic() + 2
    while fut in bg_loop._pending and time.monotonic() < deadline:
        time.sleep(0.01)
    assert fut not in bg_loop._pending


def test_run_drops_reference_on_failure():
    async def _boom():
        raise RuntimeError("x")

    fut = bg_loop.run(_boom())
    with contextlib.suppress(RuntimeError):
        fut.result(timeout=5)
    deadline = time.monotonic() + 2
    while fut in bg_loop._pending and time.monotonic() < deadline:
        time.sleep(0.01)
    assert fut not in bg_loop._pending
