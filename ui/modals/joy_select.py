# SPDX-License-Identifier: GPL-3.0-or-later
"""Joy-side selector modal — fires when a never-paired controller connects.

The controller's GATT advertisement doesn't tell us if it's the left or
right side, so we ask the user once. The choice is persisted in
``settings["devices"][address]["type"]`` so subsequent reconnects skip
this prompt.
"""
import customtkinter as ctk
from PIL import Image

from applog import get_logger
from user_preferences import save_settings, settings
from utils import resource_path

log = get_logger(__name__)


def show(parent, controller_id: str, player) -> None:
    """Open the LEFT/RIGHT picker for a freshly-paired controller.

    ``parent`` is the JoyglideUI root; we call
    ``parent.refresh_dashboard()`` after the user picks so the new
    controller appears in the list with its chosen side.
    """
    new_window = ctk.CTkToplevel(parent)
    new_window.title("New Joy-Con")
    new_window.geometry("640x300")
    new_window.lift()
    new_window.focus_force()

    def on_select(option):
        if controller_id not in settings["devices"]:
            settings["devices"][controller_id] = {"type": option}
        else:
            settings["devices"][controller_id]["type"] = option
        player.attach_joycon(option)
        save_settings(settings)
        new_window.destroy()
        parent.refresh_dashboard()

    ctk.CTkLabel(new_window, text="Which Joy-Con did you connect?",
                  font=("Helvetica", 18)).pack(pady=20)

    frame = ctk.CTkFrame(new_window, fg_color="transparent")
    frame.pack(expand=True)

    try:
        img_l = ctk.CTkImage(Image.open(resource_path("assets/left.png")),  size=(100, 200))
        img_r = ctk.CTkImage(Image.open(resource_path("assets/right.png")), size=(100, 200))
        ctk.CTkButton(frame, image=img_l, text="", command=lambda: on_select("left"),
                       fg_color="transparent", hover_color="#333333").pack(side="left", padx=20)
        ctk.CTkButton(frame, image=img_r, text="", command=lambda: on_select("right"),
                       fg_color="transparent", hover_color="#333333").pack(side="left", padx=20)
    except Exception as e:
        log.warning(f"Could not load joy-con images: {e}")
        ctk.CTkButton(frame, text="LEFT",  command=lambda: on_select("left")).pack(side="left", padx=20)
        ctk.CTkButton(frame, text="RIGHT", command=lambda: on_select("right")).pack(side="left", padx=20)
