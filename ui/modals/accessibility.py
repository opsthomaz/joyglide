# SPDX-License-Identifier: GPL-3.0-or-later
"""macOS Accessibility-permission prompt + stale-TCC-entry recovery flow.

PyInstaller ad-hoc signs every build with a fresh hash, and macOS TCC
keys Accessibility entries by (bundle_id, code-signature). When the user
replaces the .app with a new build, the old entry stays in the list
(still toggled ON) but points to a stale signature — the new binary
isn't trusted, even though the row looks fine. The "Reset Permission
and Relaunch" button clears that stale entry so the next launch can
prompt fresh.
"""
import os
import subprocess
import sys
import threading

import customtkinter as ctk

from applog import get_logger
from osio.mouse import check_accessibility
from user_preferences import settings

log = get_logger(__name__)

BUNDLE_ID = "com.opsthomaz.joyglide"


def show(parent) -> None:
    """Open the Accessibility-warning modal as a child of ``parent``.

    ``parent`` is the JoyglideUI root; we use ``parent.after`` for
    polling and ``parent.tray_connect_func`` if the user has start_with_sync
    on so syncing kicks off as soon as permission is granted.
    """
    warn_win = ctk.CTkToplevel(parent)
    warn_win.title("Permission Required")
    warn_win.geometry("520x540")
    warn_win.lift()
    warn_win.attributes("-topmost", True)

    ctk.CTkLabel(warn_win, text="⚠️ Accessibility Permission Required",
                  font=("Helvetica", 20, "bold"), text_color="#e74c3c").pack(pady=(24, 8))
    ctk.CTkLabel(warn_win,
                  text="macOS blocks Joyglide from controlling your cursor\n"
                       "until you grant it permission in System Settings.",
                  font=("Helvetica", 13)).pack(pady=4)

    def open_sys_settings():
        def _run():
            subprocess.run(["open",
                "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"])
        threading.Thread(target=_run, daemon=True, name="joyglide-open-sys-settings").start()

    ctk.CTkButton(warn_win, text="Open System Settings", fg_color="#3498db",
                   font=("Helvetica", 14, "bold"), height=40,
                   command=open_sys_settings).pack(pady=(12, 6))

    # ── "I granted — Quit & Reopen" workaround for the TCC-cache bug ──
    # macOS does not invalidate the running process's cached Accessibility
    # trust state when the user grants permission, so the polling loop
    # below often never sees the grant — the process needs a fresh start
    # to re-query TCC. Apple's own recommendation in this scenario is
    # "quit and reopen." This button automates that.
    def quit_and_relaunch():
        def _run():
            app_path = find_app_bundle_path()
            if app_path:
                parent.after(0, lambda p=app_path: subprocess.Popen(["open", "-n", p]))
            log.info("Quit & Reopen — restarting so the next process sees the granted permission.")
            parent.after(200, lambda: os._exit(0))
        threading.Thread(target=_run, daemon=True, name="joyglide-quit-relaunch").start()

    ctk.CTkButton(warn_win, text="✓ I granted — Quit & Reopen",
                   fg_color="#27ae60", hover_color="#1e8449",
                   font=("Helvetica", 13, "bold"), height=36,
                   command=quit_and_relaunch).pack(pady=(4, 2))
    ctk.CTkLabel(warn_win,
                  text="macOS doesn't notify running apps when Accessibility\n"
                       "is granted. Click here after toggling it on.",
                  font=("Helvetica", 10), text_color="gray",
                  justify="center").pack(pady=(0, 8))

    # ── Stale-entry recovery ────────────────────────────────────────
    ctk.CTkFrame(warn_win, height=1, fg_color="#444").pack(fill="x", padx=24, pady=(16, 12))
    ctk.CTkLabel(warn_win, text="Already enabled but still blocked?",
                  font=("Helvetica", 13, "bold")).pack()
    ctk.CTkLabel(warn_win,
                  text="If Joyglide already shows up enabled in the\n"
                       "Accessibility list, it may be a stale entry from an\n"
                       "earlier build. Reset clears it so a fresh prompt appears.",
                  font=("Helvetica", 11), text_color="gray", justify="center").pack(pady=4)

    def find_app_bundle_path() -> str | None:
        """Walk up sys.executable to find the enclosing ``.app`` bundle."""
        exe = sys.executable or sys.argv[0]
        path = os.path.realpath(exe)
        while path and path != "/":
            if path.endswith(".app"):
                return path
            path = os.path.dirname(path)
        return None

    def reset_permission():
        def _run():
            try:
                subprocess.run(["tccutil", "reset", "Accessibility", BUNDLE_ID],
                                check=True, capture_output=True, text=True)
                log.info(f"🔄 Reset Accessibility permission for {BUNDLE_ID}")
            except (subprocess.CalledProcessError, FileNotFoundError) as e:
                log.warning(f"⚠️ tccutil reset failed: {e}")
                parent.after(0, lambda err=e: ctk.CTkLabel(
                    warn_win,
                    text=f"Reset failed: {err}. Try running it from Terminal:\n"
                         f"tccutil reset Accessibility {BUNDLE_ID}",
                    font=("Helvetica", 11), text_color="#e74c3c",
                    justify="center").pack(pady=4))
                return

            # Relaunch via `open` if we're inside a .app bundle, otherwise
            # just exit and let the user re-run. `open -n` forces a fresh
            # process so the new instance gets a clean TCC prompt.
            app_path = find_app_bundle_path()
            if app_path:
                parent.after(0, lambda p=app_path: subprocess.Popen(["open", "-n", p]))
            log.info("Quitting so the freshly-prompted instance can take over.")
            parent.after(200, lambda: os._exit(0))

        threading.Thread(target=_run, daemon=True, name="joyglide-reset-permission").start()

    ctk.CTkButton(warn_win, text="Reset Permission and Relaunch",
                   fg_color="#7f8c8d", hover_color="#566061",
                   font=("Helvetica", 12), height=32,
                   command=reset_permission).pack(pady=(4, 8))

    poll_label = ctk.CTkLabel(warn_win, text="Waiting for permission…",
                               font=("Helvetica", 11, "italic"), text_color="gray")
    poll_label.pack(pady=(8, 0))

    poll_count = [0]

    def poll_accessibility():
        if check_accessibility():
            log.info("✅ Accessibility Permission granted interactively.")
            if warn_win.winfo_exists():
                warn_win.destroy()
            if settings.get("start_with_sync"):
                parent.tray_connect_func()
        else:
            poll_count[0] += 1
            if poll_count[0] > 15:
                poll_label.configure(
                    text="Polling timed out — macOS isn't notifying this process.\n"
                         "If you've granted permission, click the green button above.",
                    text_color="#e67e22",
                    font=("Helvetica", 11),
                )
                return
            delay = 1000 if poll_count[0] <= 5 else 3000
            parent.after(delay, poll_accessibility)

    parent.after(1000, poll_accessibility)
