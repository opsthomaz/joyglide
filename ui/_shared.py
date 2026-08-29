# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared widget helpers used across multiple tabs."""
import contextlib
import tkinter

import customtkinter as ctk

from utils import unpack_touchpad_delta


def add_divider(parent, row: int) -> None:
    """Add a 1px horizontal divider line at the given grid row."""
    ctk.CTkFrame(parent, height=1, fg_color="#3a3a3a").grid(
        row=row, column=0, sticky="ew", padx=16, pady=4)


def enable_touchpad_scroll(frame: ctk.CTkScrollableFrame) -> None:
    """Make a ``CTkScrollableFrame`` respond to Tk 9 ``<TouchpadScroll>``.

    customtkinter (≤ 6.0) only binds ``<MouseWheel>``; on macOS with
    Tk 9 (python.org 3.14.5+, Homebrew python-tk) trackpad and other
    continuous scrolling — including the Joy-Con stick's pixel-unit
    scroll events — arrive as ``<TouchpadScroll>`` instead, so the lists
    could only be scrolled by dragging the bar (customtkinter #2858).
    Deltas are pixels; scroll by moving the view by that fraction of the
    content height so it feels 1:1 like any native list. No-op on Tk 8.6
    (event type unknown → ``TclError``).
    """
    canvas = frame._parent_canvas

    def _on_touchpad(event):
        if not frame.check_if_master_is_canvas(event.widget):
            return
        dx, dy = unpack_touchpad_delta(int(event.delta))
        bbox = canvas.bbox("all")
        if bbox is None:
            return
        if dy and canvas.yview() != (0.0, 1.0):
            height = max(1, bbox[3] - bbox[1])
            canvas.yview_moveto(canvas.yview()[0] - dy / height)
        if dx and canvas.xview() != (0.0, 1.0):
            width = max(1, bbox[2] - bbox[0])
            canvas.xview_moveto(canvas.xview()[0] - dx / width)

    # Tk 8.6 doesn't know the event type; there <MouseWheel> already covers everything.
    with contextlib.suppress(tkinter.TclError):
        frame.bind_all("<TouchpadScroll>", _on_touchpad, add="+")
