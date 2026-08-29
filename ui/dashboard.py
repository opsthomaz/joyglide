# SPDX-License-Identifier: GPL-3.0-or-later
"""Dashboard tab — list of connected controllers with battery/side/actions."""
import contextlib

import customtkinter as ctk

from ui._shared import enable_touchpad_scroll


class DashboardMixin:
    """Adds dashboard-tab construction + refresh logic to ``JoyglideUI``.

    Provides:
      * ``setup_dashboard()`` — builds widgets in the Dashboard tab.
      * ``refresh_dashboard()`` — tears down + rebuilds the per-controller
        rows. Called on a 1Hz tick (battery moves slowly, this is plenty)
        and on every ``player_list_changed`` event from the queue.
    """

    def setup_dashboard(self):
        """Build the Dashboard tab: status label, scrollable controllers
        frame, and the Sync button. Kicks off the 1Hz refresh tick."""
        tab = self.tabview.tab("Dashboard")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        # Top status line — global state ("connecting…", "paused", etc.)
        self.status_label = ctk.CTkLabel(tab, text="No controllers connected",
                                          font=("Helvetica", 16))
        self.status_label.grid(row=0, column=0, pady=(20, 8))

        # Per-controller list (populated by refresh_dashboard)
        self.controllers_frame = ctk.CTkScrollableFrame(tab, label_text="Controllers")
        self.controllers_frame.grid(row=1, column=0, sticky="nsew", padx=20, pady=8)
        self.controllers_frame.grid_columnconfigure(0, weight=1)
        enable_touchpad_scroll(self.controllers_frame)

        self.sync_button = ctk.CTkButton(tab, text="+ Sync New Controller",
                                          font=("Helvetica", 14, "bold"),
                                          height=40,
                                          command=self.tray_connect_func)
        self.sync_button.grid(row=2, column=0, pady=(8, 16))

        self.refresh_dashboard()
        # Periodic refresh — battery moves slowly, this is plenty.
        self.after(1000, self._dashboard_tick)

    def _set_sync_active(self, active: bool) -> None:
        """Toggle the Sync button between idle and "in progress" states.

        Called from ``process_queue`` when a ``sync_state`` event arrives.
        Disables the button while a scan/connect flow is in flight so
        the user can't queue overlapping scans (which would share the
        ``used_addresses`` set and produce the "click does nothing"
        symptom).
        """
        if active:
            self.sync_button.configure(text="Searching…", state="disabled")
        else:
            self.sync_button.configure(text="+ Sync New Controller", state="normal")

    def _dashboard_tick(self):
        """1Hz periodic tick — refresh + reschedule. Picks up battery
        changes without needing explicit invalidation events."""
        self.refresh_dashboard()
        self.after(1000, self._dashboard_tick)

    def _format_battery(self, gp):
        """Pretty-print a battery row from a JoyCon gamepad. Handles the
        None/missing/full/charging cases and picks an icon by level.

        Battery current is the 2-byte field at input-report 0x05 offset
        0x22. Raw value scaled by 1/100 to get mA — confirmed by
        TropicalCyclone's working driver and an 818-second hardware
        capture (raw 1820 / 100 = 18.2 mA matches the JC2's 525 mAh
        / 20-h spec). See ``parser.battery`` for the full derivation.
        """
        if gp is None or gp.battery_pct is None:
            return "battery: —"
        ma = gp.battery_current_ma
        cur = f"  ⚡{ma:.1f} mA" if ma is not None else ""
        if gp.battery_full:
            return f"🔌 {gp.battery_pct}% (full){cur}"
        if gp.battery_charging:
            return f"⚡ {gp.battery_pct}% (charging){cur}"
        if   gp.battery_pct >= 60: icon = "🔋"   # high
        elif gp.battery_pct >= 20: icon = "🪫"   # medium
        else:                      icon = "🪫"   # low (no distinct critical-low emoji available)
        return f"{icon} {gp.battery_pct}%  ({gp.battery_mv} mV){cur}"

    def refresh_dashboard(self):
        """Tear down + rebuild every per-controller row from the current
        ``self.players`` snapshot. Cheap because the list is tiny
        (maximum 8 controllers in practice)."""
        # Tear down previous rows (cheap, the list is tiny — max 8 controllers).
        for w in self._row_widgets:
            with contextlib.suppress(Exception):
                w.destroy()
        self._row_widgets.clear()

        # Snapshot the players list before iterating. self.players points to
        # the same list main.py mutates from BLE threads — without the
        # snapshot, a concurrent connect/disconnect during a UI refresh could
        # raise RuntimeError ("list changed size during iteration") on Tk's
        # main thread, freezing the dashboard.
        players_snapshot = list(self.players)

        if not players_snapshot:
            empty = ctk.CTkLabel(self.controllers_frame,
                                  text="No controllers connected.\nClick \"Sync New Controller\" below.",
                                  font=("Helvetica", 13), text_color="#888888",
                                  justify="center")
            empty.grid(row=0, column=0, pady=24)
            self._row_widgets.append(empty)
            return

        for i, player in enumerate(players_snapshot):
            row = ctk.CTkFrame(self.controllers_frame, corner_radius=8)
            row.grid(row=i, column=0, sticky="ew", padx=4, pady=4)
            row.grid_columnconfigure(1, weight=1)

            side_text = (player.side or "?").upper()
            side_color = "#5dade2" if player.side == "left" else "#e67e22"
            side_lbl = ctk.CTkLabel(row, text=side_text,
                                     font=("Helvetica", 14, "bold"),
                                     text_color=side_color, width=60)
            side_lbl.grid(row=0, column=0, padx=(12, 8), pady=10)

            short_addr = (player.address or "")[-8:] if player.address else ""
            info_text = f"P{player.number} · {short_addr}\n{self._format_battery(player.gamepad)}"
            info_lbl = ctk.CTkLabel(row, text=info_text, justify="left",
                                     font=("Helvetica", 12))
            info_lbl.grid(row=0, column=1, sticky="w", pady=10)

            # Switch side
            ctk.CTkButton(row, text="Switch Side", width=110,
                           fg_color="#3a4a5e", hover_color="#4a5a6e",
                           command=lambda p=player: self.switch_side_fn(p)).grid(
                row=0, column=2, padx=4, pady=10)
            # Disconnect
            ctk.CTkButton(row, text="Disconnect", width=110,
                           fg_color="#7f3a3a", hover_color="#a04848",
                           command=lambda p=player: self.disconnect_fn(p)).grid(
                row=0, column=3, padx=(4, 12), pady=10)

            self._row_widgets.append(row)
