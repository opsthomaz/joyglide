# SPDX-License-Identifier: GPL-3.0-or-later
"""Settings tab — startup / input / hardware / debug toggles + reset."""
import customtkinter as ctk

from ui._shared import add_divider
from user_preferences import save_settings, settings


class SettingsMixin:
    """Adds the Settings tab to ``JoyglideUI``.

    Exposes Tk variables (``self.start_sync_var``, ``self.ignore_win_var``,
    ``self.double_click_var``, ``self.swap_click_var``, ``self.vibration_var``,
    ``self.gatt_dump_var``) that ``reset_to_defaults`` reaches into to
    refresh the UI when the user nukes their preferences.
    """

    def setup_settings(self):
        """Build the Settings tab — startup / input / hardware / debug
        switches plus the Reset button. Each switch's command points at
        ``save_settings_tab`` so toggling persists immediately."""
        tab = self.tabview.tab("Settings")
        tab.grid_columnconfigure(0, weight=1)

        row = 0

        # — Startup —
        ctk.CTkLabel(tab, text="Startup",
                     font=("Helvetica", 15, "bold")).grid(
            row=row, column=0, pady=(16, 6), sticky="w", padx=16)
        row += 1

        self.start_sync_var = ctk.BooleanVar(value=settings.get("start_with_sync", False))
        ctk.CTkSwitch(tab, text="Sync controller on launch",
                       variable=self.start_sync_var,
                       command=self.save_settings_tab).grid(
            row=row, column=0, pady=6, padx=16, sticky="w")
        row += 1

        self.ignore_win_var = ctk.BooleanVar(value=settings.get("ignore_opening_window", False))
        ctk.CTkSwitch(tab, text="Start minimized in tray",
                       variable=self.ignore_win_var,
                       command=self.save_settings_tab).grid(
            row=row, column=0, pady=6, padx=16, sticky="w")
        row += 1

        add_divider(tab, row); row += 1

        # — Input —
        ctk.CTkLabel(tab, text="Input",
                     font=("Helvetica", 15, "bold")).grid(
            row=row, column=0, pady=(12, 6), sticky="w", padx=16)
        row += 1

        self.double_click_var = ctk.BooleanVar(value=settings.get("double_click_enabled", True))
        ctk.CTkSwitch(tab, text="Double-click detection (rapid press = double-click)",
                       variable=self.double_click_var,
                       command=self.save_settings_tab).grid(
            row=row, column=0, pady=6, padx=16, sticky="w")
        row += 1

        self.swap_click_var = ctk.BooleanVar(value=settings.get("swap_click_buttons", False))
        ctk.CTkSwitch(tab, text="Swap click buttons (trigger ZL/ZR = left click, shoulder L/R = right)",
                       variable=self.swap_click_var,
                       command=self.save_settings_tab).grid(
            row=row, column=0, pady=6, padx=16, sticky="w")
        row += 1

        add_divider(tab, row); row += 1

        # — Hardware —
        ctk.CTkLabel(tab, text="Hardware",
                     font=("Helvetica", 15, "bold")).grid(
            row=row, column=0, pady=(12, 6), sticky="w", padx=16)
        row += 1

        self.vibration_var = ctk.BooleanVar(value=settings.get("vibration_on_connect", True))
        ctk.CTkSwitch(tab, text="Vibration feedback on connect",
                       variable=self.vibration_var,
                       command=self.save_settings_tab).grid(
            row=row, column=0, pady=6, padx=16, sticky="w")
        row += 1

        add_divider(tab, row); row += 1

        # — Debug —
        ctk.CTkLabel(tab, text="Debug",
                     font=("Helvetica", 15, "bold")).grid(
            row=row, column=0, pady=(12, 6), sticky="w", padx=16)
        row += 1

        self.gatt_dump_var = ctk.BooleanVar(value=settings.get("show_gatt_dump", False))
        ctk.CTkSwitch(tab, text="Print GATT profile on connect (terminal)",
                       variable=self.gatt_dump_var,
                       command=self.save_settings_tab).grid(
            row=row, column=0, pady=6, padx=16, sticky="w")
        row += 1

        add_divider(tab, row); row += 1

        # — Reset —
        ctk.CTkButton(tab, text="Reset all preferences to defaults",
                       fg_color="#7f3a3a", hover_color="#a04848",
                       command=self.reset_to_defaults).grid(
            row=row, column=0, pady=(12, 16), padx=16, sticky="w")
        row += 1

    def reset_to_defaults(self):
        """Open a confirm dialog; on accept, copy every key from
        ``DEFAULTS`` back into the settings dict (preserving the paired-
        controllers list), persist, and refresh BOTH tabs' displayed
        values via ``update_from_settings`` and direct var.set calls."""
        confirm = ctk.CTkToplevel(self)
        confirm.title("Reset preferences?")
        confirm.geometry("420x180")
        confirm.lift()
        confirm.attributes("-topmost", True)
        ctk.CTkLabel(confirm,
                      text="Reset all preferences to defaults?\n"
                           "Paired controllers will be kept.",
                      font=("Helvetica", 14)).pack(pady=(24, 12))
        btns = ctk.CTkFrame(confirm, fg_color="transparent")
        btns.pack(pady=12)

        def do_reset():
            from user_preferences import DEFAULTS
            preserved_devices = settings.get("devices", {})
            for k, v in DEFAULTS.items():
                settings[k] = v.copy() if isinstance(v, dict) else v
            settings["devices"] = preserved_devices
            save_settings(settings)
            self.update_from_settings()
            # Also reflect the toggles on the Settings tab.
            self.start_sync_var.set(settings["start_with_sync"])
            self.ignore_win_var.set(settings["ignore_opening_window"])
            self.double_click_var.set(settings["double_click_enabled"])
            self.swap_click_var.set(settings["swap_click_buttons"])
            self.vibration_var.set(settings["vibration_on_connect"])
            self.gatt_dump_var.set(settings["show_gatt_dump"])
            self.refresh_button_map_from_settings()
            confirm.destroy()

        ctk.CTkButton(btns, text="Reset", fg_color="#a04848",
                       command=do_reset).pack(side="left", padx=8)
        ctk.CTkButton(btns, text="Cancel",
                       command=confirm.destroy).pack(side="left", padx=8)

    def save_settings_tab(self):
        """Pull every Settings-tab Tk variable into the settings dict
        and persist immediately. Unlike Performance, these are toggles
        (no slider drag), so no debounce is needed."""
        settings["start_with_sync"]       = self.start_sync_var.get()
        settings["ignore_opening_window"] = self.ignore_win_var.get()
        settings["double_click_enabled"]  = self.double_click_var.get()
        settings["swap_click_buttons"]    = self.swap_click_var.get()
        settings["vibration_on_connect"]  = self.vibration_var.get()
        settings["show_gatt_dump"]        = self.gatt_dump_var.get()
        save_settings(settings)
