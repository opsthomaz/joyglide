# SPDX-License-Identifier: GPL-3.0-or-later
"""Buttons tab — per-button action mapping (``settings["button_map"]``)."""
import customtkinter as ctk

from user_preferences import BUTTON_ACTIONS, DEFAULT_BUTTON_MAP, save_settings, settings

# Display order + labels. Physical location in parentheses so a user
# holding a single Joy-Con can find the button without the manual.
_BUTTON_LABELS = [
    ("L",     "L  (left shoulder)"),
    ("ZL",    "ZL (left trigger)"),
    ("R",     "R  (right shoulder)"),
    ("ZR",    "ZR (right trigger)"),
    ("STICK", "Stick press (either side)"),
    ("A",     "A"),
    ("Y",     "Y"),
    ("LEFT",  "D-pad ←"),
    ("RIGHT", "D-pad →"),
]

_ACTION_LABELS = {
    "left":    "Left click",
    "right":   "Right click",
    "middle":  "Middle click",
    "back":    "Back",
    "forward": "Forward",
    "none":    "Nothing",
}
_LABEL_TO_ACTION = {v: k for k, v in _ACTION_LABELS.items()}


class ButtonsMixin:
    """Adds the Buttons tab to ``JoyglideUI``.

    One option menu per mappable button. Changes write straight into
    ``settings["button_map"]`` (the parser reads it on every button
    transition) and persist immediately — toggles, no debounce needed.
    """

    def setup_buttons(self):
        """Build the Buttons tab: a scrollable list of button → action
        menus plus a reset-to-default button."""
        tab = self.tabview.tab("Buttons")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)

        scroll = ctk.CTkScrollableFrame(tab)
        scroll.grid(row=0, column=0, sticky="nsew", padx=4)
        scroll.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(scroll, text="Button mapping",
                     font=("Helvetica", 15, "bold")).grid(
            row=0, column=0, columnspan=2, pady=(10, 2), sticky="w", padx=16)
        ctk.CTkLabel(scroll,
                     text="\"Swap click buttons\" in Settings flips left/right on top of this.",
                     font=("Helvetica", 11), text_color="gray").grid(
            row=1, column=0, columnspan=2, pady=(0, 8), sticky="w", padx=16)

        self.button_map_vars: dict[str, ctk.StringVar] = {}
        current = settings.get("button_map", DEFAULT_BUTTON_MAP)
        for i, (btn, label) in enumerate(_BUTTON_LABELS, start=2):
            ctk.CTkLabel(scroll, text=label, font=("Helvetica", 13)).grid(
                row=i, column=0, sticky="w", padx=16, pady=4)
            var = ctk.StringVar(value=_ACTION_LABELS[current.get(btn, DEFAULT_BUTTON_MAP[btn])])
            self.button_map_vars[btn] = var
            ctk.CTkOptionMenu(scroll, variable=var, width=150,
                              values=[_ACTION_LABELS[a] for a in BUTTON_ACTIONS],
                              command=lambda _v, b=btn: self._save_button_map(b)).grid(
                row=i, column=1, sticky="e", padx=16, pady=4)

        ctk.CTkButton(scroll, text="Reset mapping to defaults",
                      fg_color="#3a4a5e", hover_color="#4a5a6e",
                      command=self.reset_button_map).grid(
            row=len(_BUTTON_LABELS) + 2, column=0, columnspan=2,
            pady=(12, 16), padx=16, sticky="w")

    def _save_button_map(self, _btn: str) -> None:
        """Write every menu's value into ``settings["button_map"]`` and persist."""
        settings["button_map"] = {
            btn: _LABEL_TO_ACTION[var.get()] for btn, var in self.button_map_vars.items()
        }
        save_settings(settings)

    def reset_button_map(self) -> None:
        """Restore ``DEFAULT_BUTTON_MAP`` in the menus and on disk."""
        for btn, var in self.button_map_vars.items():
            var.set(_ACTION_LABELS[DEFAULT_BUTTON_MAP[btn]])
        self._save_button_map("")

    def refresh_button_map_from_settings(self) -> None:
        """Pull the menus from the settings dict (after a global reset)."""
        current = settings.get("button_map", DEFAULT_BUTTON_MAP)
        for btn, var in self.button_map_vars.items():
            var.set(_ACTION_LABELS[current.get(btn, DEFAULT_BUTTON_MAP[btn])])
