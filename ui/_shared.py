# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared widget helpers used across multiple tabs."""
import customtkinter as ctk


def add_divider(parent, row: int) -> None:
    """Add a 1px horizontal divider line at the given grid row."""
    ctk.CTkFrame(parent, height=1, fg_color="#3a3a3a").grid(
        row=row, column=0, sticky="ew", padx=16, pady=4)
