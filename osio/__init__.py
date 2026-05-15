# SPDX-License-Identifier: GPL-3.0-or-later
"""OS-specific I/O backends — cursor injection, global hotkey, BLE rate boost.

Each subsystem follows the same dispatcher pattern: a top-level module
that picks the right backend by ``sys.platform`` and re-exports the
public API. Adding a new OS = creating a new ``<subsystem>/<os>.py``
file and extending the dispatcher.

  * ``osio.mouse``  — cursor injection
                      (macos: Quartz CGEventPost, windows: SendInput)
  * ``osio.hotkey`` — global pause hotkey
                      (macos: CGEventTap, windows: RegisterHotKey)
  * ``osio.boost``  — process priority + anti-throttle + BLE rate
                      negotiation (macOS: NSActivity + os.nice;
                      Windows: HIGH_PRIORITY_CLASS + ThroughputOptimized)
"""
