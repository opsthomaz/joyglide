# SPDX-License-Identifier: GPL-3.0-or-later
"""Buttons tab — per-button action mapping (``settings["button_map"]``)."""
import customtkinter as ctk

from user_preferences import (
    BUTTON_ACTIONS,
    DEFAULT_BUTTON_MAP,
    save_settings,
    settings,
    sides_without_click,
)

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

    One option menu per mappable button. The menus only edit a pending
    layout; **Apply** writes it into ``settings["button_map"]`` (the
    parser reads it on every button transition) and persists it. That
    way the user can e.g. set R = right click and ZR = left click in two
    steps without the intermediate "two right clicks, no left click"
    layout ever being live.
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
                     text="Changes take effect when you press Apply.\n"
                          "\"Swap click buttons\" in Settings flips left/right on top of this.",
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
                              command=lambda _v: self._mark_pending()).grid(
                row=i, column=1, sticky="e", padx=16, pady=4)

        buttons_row = len(_BUTTON_LABELS) + 2
        actions = ctk.CTkFrame(scroll, fg_color="transparent")
        actions.grid(row=buttons_row, column=0, columnspan=2, pady=(12, 4), padx=16, sticky="w")
        ctk.CTkButton(actions, text="Apply", width=110,
                      command=self.apply_button_map).pack(side="left", padx=(0, 8))
        ctk.CTkButton(actions, text="Reset to defaults", width=140,
                      fg_color="#3a4a5e", hover_color="#4a5a6e",
                      command=self.reset_button_map).pack(side="left")
        self.button_map_status = ctk.CTkLabel(scroll, text="", font=("Helvetica", 11),
                                              text_color="gray")
        self.button_map_status.grid(row=buttons_row + 1, column=0, columnspan=2,
                                    pady=(0, 16), padx=16, sticky="w")

    def _pending_map(self) -> dict:
        return {btn: _LABEL_TO_ACTION[var.get()] for btn, var in self.button_map_vars.items()}

    def _mark_pending(self) -> None:
        """A menu changed — nothing is live until Apply."""
        self.button_map_status.configure(text="Unapplied changes — press Apply.",
                                         text_color="#f1c40f")

    def apply_button_map(self) -> None:
        """Write the pending layout into ``settings["button_map"]``, persist,
        and warn if a Joy-Con side ended up with no click at all."""
        settings["button_map"] = self._pending_map()
        save_settings(settings)
        missing = sides_without_click(settings["button_map"])
        if missing:
            self.button_map_status.configure(
                text=f"Applied — note: no left/right click on the {' and '.join(missing)} Joy-Con.",
                text_color="#e67e22")
        else:
            self.button_map_status.configure(text="Applied.", text_color="#2ecc71")

    def reset_button_map(self) -> None:
        """Restore ``DEFAULT_BUTTON_MAP`` in the menus and apply it."""
        for btn, var in self.button_map_vars.items():
            var.set(_ACTION_LABELS[DEFAULT_BUTTON_MAP[btn]])
        self.apply_button_map()

    def refresh_button_map_from_settings(self) -> None:
        """Pull the menus from the settings dict (after a global reset)."""
        current = settings.get("button_map", DEFAULT_BUTTON_MAP)
        for btn, var in self.button_map_vars.items():
            var.set(_ACTION_LABELS[current.get(btn, DEFAULT_BUTTON_MAP[btn])])
        self.button_map_status.configure(text="", text_color="gray")
