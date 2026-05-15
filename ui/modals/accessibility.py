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
    warn_win.geometry("520x460")
    warn_win.lift()
    warn_win.attributes("-topmost", True)

    ctk.CTkLabel(warn_win, text="⚠️ Accessibility Permission Required",
                  font=("Helvetica", 20, "bold"), text_color="#e74c3c").pack(pady=(24, 8))
    ctk.CTkLabel(warn_win,
                  text="macOS blocks Joyglide from controlling your cursor\n"
                       "until you grant it permission in System Settings.",
                  font=("Helvetica", 13)).pack(pady=4)

    def open_sys_settings():
        subprocess.run(["open",
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"])

    ctk.CTkButton(warn_win, text="Open System Settings", fg_color="#3498db",
                   font=("Helvetica", 14, "bold"), height=40,
                   command=open_sys_settings).pack(pady=(12, 6))

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
        try:
            subprocess.run(["tccutil", "reset", "Accessibility", BUNDLE_ID],
                            check=True, capture_output=True, text=True)
            log.info(f"🔄 Reset Accessibility permission for {BUNDLE_ID}")
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            log.warning(f"⚠️ tccutil reset failed: {e}")
            ctk.CTkLabel(warn_win,
                          text=f"Reset failed: {e}. Try running it from Terminal:\n"
                               f"tccutil reset Accessibility {BUNDLE_ID}",
                          font=("Helvetica", 11), text_color="#e74c3c",
                          justify="center").pack(pady=4)
            return

        # Relaunch via `open` if we're inside a .app bundle, otherwise
        # just exit and let the user re-run. `open -n` forces a fresh
        # process so the new instance gets a clean TCC prompt.
        app_path = find_app_bundle_path()
        if app_path:
            subprocess.Popen(["open", "-n", app_path])
        log.info("Quitting so the freshly-prompted instance can take over.")
        parent.after(200, lambda: os._exit(0))

    ctk.CTkButton(warn_win, text="Reset Permission and Relaunch",
                   fg_color="#7f8c8d", hover_color="#566061",
                   font=("Helvetica", 12), height=32,
                   command=reset_permission).pack(pady=(4, 8))

    ctk.CTkLabel(warn_win, text="Waiting for permission…",
                  font=("Helvetica", 11, "italic"), text_color="gray").pack(pady=(8, 0))

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
                return
            delay = 1000 if poll_count[0] <= 5 else 3000
            parent.after(delay, poll_accessibility)

    parent.after(1000, poll_accessibility)
