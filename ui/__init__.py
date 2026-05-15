# SPDX-License-Identifier: GPL-3.0-or-later
"""CustomTkinter UI for Joyglide — composed of three tab mixins.

Three tabs:
  * **Dashboard** (``ui.dashboard.DashboardMixin``) — list of currently
    connected controllers with battery, side, and per-row Disconnect /
    Switch Side buttons. Refreshes every 1 second to pick up battery
    changes.
  * **Performance** (``ui.performance.PerformanceMixin``) — motion
    profile selector (Dynamic / Gaming / Cinematic), sensitivity /
    deadzone / acceleration sliders, scroll speed.
  * **Settings** (``ui.settings_tab.SettingsMixin``) — startup behaviour
    (auto-sync, start minimised), input options (double-click), hardware
    (vibration on connect, GATT dump), reset.

Modal windows live in ``ui.modals``.

Cross-thread comms with the BLE / asyncio threads goes through
``command_queue`` (a stdlib ``queue.Queue``) — they enqueue events, the
Tk mainloop drains it on a 100ms timer in ``process_queue``. The UI
never reaches into the BLE threads directly.
"""
import queue

import customtkinter as ctk

from ui.dashboard import DashboardMixin
from ui.modals import accessibility as modal_accessibility
from ui.modals import joy_select as modal_joy_select
from ui.performance import PerformanceMixin
from ui.settings_tab import SettingsMixin


class JoyglideUI(DashboardMixin, PerformanceMixin, SettingsMixin, ctk.CTk):
    def __init__(self, command_queue, tray_connect_func,
                 players_ref=None, disconnect_fn=None, switch_side_fn=None):
        super().__init__()

        self.command_queue   = command_queue
        self.tray_connect_func = tray_connect_func
        self.players         = players_ref if players_ref is not None else []
        self.disconnect_fn   = disconnect_fn   or (lambda p: None)
        self.switch_side_fn  = switch_side_fn  or (lambda p: None)
        self._row_widgets    = []  # keeps refs to dashboard row frames

        self.title("Joyglide")
        self.geometry("620x560")

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.protocol("WM_DELETE_WINDOW", self.withdraw)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.tabview = ctk.CTkTabview(self)
        self.tabview.grid(row=0, column=0, padx=20, pady=20, sticky="nsew")

        self.tabview.add("Dashboard")
        self.tabview.add("Performance")
        self.tabview.add("Settings")

        self.setup_dashboard()
        self.setup_performance()
        self.setup_settings()

        self.after(100, self.process_queue)

    def process_queue(self):
        """Drain the cross-thread event queue. Runs on the Tk mainloop
        every 100ms. Each event type maps to a method on one of the
        mixins or a modal in ``ui.modals``."""
        try:
            while True:
                command  = self.command_queue.get_nowait()
                cmd_type = command.get("type")
                cmd_data = command.get("data", {})

                if cmd_type == "new_joy_window":
                    modal_joy_select.show(self,
                                          cmd_data.get("controller_id"),
                                          cmd_data.get("player"))
                elif cmd_type == "show_dashboard":
                    # Tray "Open Dashboard" — handled here (not directly in
                    # the tray callback) so the AppKit menu callback returns
                    # immediately instead of blocking the main thread on a
                    # synchronous Tkapp_Call.
                    self.deiconify()
                    self.lift()
                    self.update_from_settings()
                elif cmd_type == "update_settings":
                    self.update_from_settings()
                elif cmd_type == "show_accessibility_warning":
                    modal_accessibility.show(self)
                elif cmd_type == "status_update":
                    self.status_label.configure(
                        text=cmd_data.get("text", ""),
                        text_color=cmd_data.get("color", "#ffffff"))
                elif cmd_type == "player_list_changed":
                    self.refresh_dashboard()
                elif cmd_type == "sync_state":
                    self._set_sync_active(cmd_data.get("active", False))
                elif cmd_type == "pause_state":
                    paused = cmd_data.get("paused", False)
                    if paused:
                        self.status_label.configure(
                            text="⏸  Paused — press ⌃⌥M to resume",
                            text_color="#f1c40f")
                    else:
                        self.status_label.configure(
                            text="▶  Active",
                            text_color="#2ecc71")
        except queue.Empty:
            pass
        self.after(100, self.process_queue)
