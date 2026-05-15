# SPDX-License-Identifier: GPL-3.0-or-later
"""Performance tab — motion profile + sensitivity / deadzone / accel sliders.

Settings writes are debounced to 300ms in ``save_perf`` so dragging a
slider doesn't hammer the JSON file 30+ times per drag.
"""
import contextlib

import customtkinter as ctk

from ui._shared import add_divider
from user_preferences import save_settings, settings


class PerformanceMixin:
    """Adds the Performance tab to ``JoyglideUI``.

    Exposes Tk variables (``self.profile_var``, ``self.sensitivity_var``,
    ``self.deadzone_var``, ``self.disable_accel_var``,
    ``self.accel_level_var``, ``self.scroll_sens_var``) used by the
    sliders/radios. ``update_from_settings`` (also defined here)
    re-reads the settings dict into those vars so the Settings tab's
    Reset button can refresh the Performance tab in place.
    """

    def setup_performance(self):
        """Build the Performance tab — profile radios, sensitivity /
        deadzone / acceleration sliders, scroll speed. Wires every
        widget's command to ``save_perf`` so changes flow into the
        in-memory settings dict immediately and disk-flush after a 300ms
        debounce."""
        tab = self.tabview.tab("Performance")
        tab.grid_columnconfigure(0, weight=1)

        scroll = ctk.CTkScrollableFrame(tab)
        scroll.grid(row=0, column=0, sticky="nsew", padx=4)
        scroll.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)

        row = 0

        # — Motion Profile —
        ctk.CTkLabel(scroll, text="Motion Profile",
                     font=("Helvetica", 15, "bold")).grid(
            row=row, column=0, pady=(10, 4), sticky="w", padx=16)
        row += 1

        self.profile_var = ctk.StringVar(value=settings.get("profile", "dynamic"))
        pf = ctk.CTkFrame(scroll)
        pf.grid(row=row, column=0, padx=16, sticky="ew")
        pf.grid_columnconfigure((0, 1, 2), weight=1)
        ctk.CTkRadioButton(pf, text="🚀 Dynamic\n(Smart)", variable=self.profile_var,
                            value="dynamic", command=self.save_perf).grid(
            row=0, column=0, pady=10, padx=10)
        ctk.CTkRadioButton(pf, text="🎯 Gaming\n(Raw 1:1)", variable=self.profile_var,
                            value="gaming", command=self.save_perf).grid(
            row=0, column=1, pady=10, padx=10)
        ctk.CTkRadioButton(pf, text="🍿 Cinematic\n(Smooth)", variable=self.profile_var,
                            value="cinematic", command=self.save_perf).grid(
            row=0, column=2, pady=10, padx=10)
        row += 1

        add_divider(scroll, row); row += 1

        # — Sensitivity —
        ctk.CTkLabel(scroll, text="Sensitivity",
                     font=("Helvetica", 15, "bold")).grid(
            row=row, column=0, pady=(10, 2), sticky="w", padx=16)
        row += 1

        self.sensitivity_var = ctk.DoubleVar(value=settings.get("sensitivity", 1.0))
        self.sensitivity_label = ctk.CTkLabel(scroll,
            text=f"Mouse Speed: {self.sensitivity_var.get():.1f}x")
        self.sensitivity_label.grid(row=row, column=0, sticky="w", padx=16)
        row += 1
        # CTkSlider's type stub annotates from_/to as int but at runtime it
        # accepts float — needed here for sub-integer steps (0.5..3.0 in 0.1
        # increments). Our pyright config silences reportArgumentType.
        self.sensitivity_slider = ctk.CTkSlider(
            scroll, from_=0.5, to=3.0, number_of_steps=25,
            variable=self.sensitivity_var,
            command=lambda v: (
                self.sensitivity_label.configure(text=f"Mouse Speed: {float(v):.1f}x"),
                self.save_perf()
            ))
        self.sensitivity_slider.grid(row=row, column=0, pady=(0, 10), padx=16, sticky="ew")
        row += 1

        # — Deadzone —
        self.deadzone_var = ctk.IntVar(value=settings.get("deadzone", 2))
        self.deadzone_label = ctk.CTkLabel(scroll,
            text=f"Deadzone: {self.deadzone_var.get()} px")
        self.deadzone_label.grid(row=row, column=0, sticky="w", padx=16)
        row += 1
        self.deadzone_slider = ctk.CTkSlider(
            scroll, from_=0, to=8, number_of_steps=8,
            variable=self.deadzone_var,
            command=lambda v: (
                self.deadzone_label.configure(text=f"Deadzone: {int(float(v))} px"),
                self.save_perf()
            ))
        self.deadzone_slider.grid(row=row, column=0, pady=(0, 10), padx=16, sticky="ew")
        row += 1

        add_divider(scroll, row); row += 1

        # — Acceleration —
        ctk.CTkLabel(scroll, text="Acceleration",
                     font=("Helvetica", 15, "bold")).grid(
            row=row, column=0, pady=(10, 4), sticky="w", padx=16)
        row += 1

        self.disable_accel_var = ctk.BooleanVar(value=settings.get("disable_acceleration", True))
        ctk.CTkSwitch(scroll, text="Disable Acceleration (Force Raw)",
                       variable=self.disable_accel_var,
                       command=self.save_perf).grid(
            row=row, column=0, pady=4, padx=16, sticky="w")
        row += 1

        self.accel_level_var = ctk.IntVar(value=settings.get("acceleration_level", 2))
        _accel_names = {1: "Low (1.5×)", 2: "Medium (2.5×)", 3: "High (3.5×)"}
        self._accel_names = _accel_names
        self.accel_label = ctk.CTkLabel(scroll,
            text=f"Acceleration Curve: {_accel_names[self.accel_level_var.get()]}")
        self.accel_label.grid(row=row, column=0, pady=(8, 0), padx=16, sticky="w")
        row += 1
        self.accel_slider = ctk.CTkSlider(
            scroll, from_=1, to=3, number_of_steps=2,
            variable=self.accel_level_var,
            command=lambda v: (
                self.accel_label.configure(text=f"Acceleration Curve: {_accel_names[int(float(v))]}"),
                self.save_perf()
            ))
        self.accel_slider.grid(row=row, column=0, pady=(0, 10), padx=16, sticky="ew")
        row += 1

        add_divider(scroll, row); row += 1

        # — Scroll —
        ctk.CTkLabel(scroll, text="Scroll",
                     font=("Helvetica", 15, "bold")).grid(
            row=row, column=0, pady=(10, 2), sticky="w", padx=16)
        row += 1

        self.scroll_sens_var = ctk.IntVar(value=settings.get("scroll_sensitivity", 4))
        self.scroll_label = ctk.CTkLabel(scroll,
            text=f"Scroll Speed: {self.scroll_sens_var.get()}")
        self.scroll_label.grid(row=row, column=0, sticky="w", padx=16)
        row += 1
        ctk.CTkSlider(
            scroll, from_=1, to=10, number_of_steps=9,
            variable=self.scroll_sens_var,
            command=lambda v: (
                self.scroll_label.configure(text=f"Scroll Speed: {int(float(v))}"),
                self.save_perf()
            )).grid(row=row, column=0, pady=(0, 16), padx=16, sticky="ew")
        row += 1

        self.update_ui_states()

    def update_ui_states(self):
        """Re-evaluate widget enable/disable states based on current
        settings. Acceleration slider only matters in Dynamic profile
        with raw-mode off; deadzone is forced to 0 in Gaming so we
        disable that slider too — visual indicator that the value is
        ignored at the engine level."""
        # The acceleration slider is only meaningful in Dynamic profile
        # with raw-mode off. In any other configuration it has no effect,
        # so we disable it visually to avoid confusing the user.
        accel_active = (not self.disable_accel_var.get()
                        and self.profile_var.get() == "dynamic")
        self.accel_slider.configure(state="normal" if accel_active else "disabled")

        # Gaming forces deadzone to 0 internally (parser/mouse_optical.py),
        # so disable the slider in that profile to make it visually obvious.
        dead_locked = self.profile_var.get() == "gaming"
        self.deadzone_slider.configure(state="disabled" if dead_locked else "normal")

    def save_perf(self, *_):
        """Persist all Performance-tab values into the settings dict
        (in-memory updates are immediate; disk flush is debounced
        300ms via Tk's after timer to avoid hammering JSON during
        slider drags)."""
        # Update the in-memory settings dict immediately (the BLE threads
        # read it on every packet, so backend reacts within one BLE tick),
        # then debounce the disk write by 300ms — avoids writing JSON 30+
        # times during a single slider drag.
        settings["profile"]              = self.profile_var.get()
        settings["sensitivity"]          = round(self.sensitivity_var.get(), 2)
        settings["deadzone"]             = int(self.deadzone_var.get())
        settings["disable_acceleration"] = self.disable_accel_var.get()
        settings["acceleration_level"]   = int(self.accel_level_var.get())
        settings["scroll_sensitivity"]   = int(self.scroll_sens_var.get())
        self.update_ui_states()

        if getattr(self, "_save_perf_after_id", None) is not None:
            with contextlib.suppress(Exception):
                self.after_cancel(self._save_perf_after_id)
        self._save_perf_after_id = self.after(300, self._flush_save_perf)

    def _flush_save_perf(self):
        """Debounced disk write — fires 300ms after the last save_perf
        call. Clears the after-id so the next save_perf can re-arm."""
        self._save_perf_after_id = None
        save_settings(settings)

    def update_from_settings(self):
        """Pull every Performance-tab Tk variable from the current
        settings dict, refreshing the displayed values + slider
        positions. Called by the queue handler when ``update_settings``
        events arrive (e.g., after the Settings-tab Reset button)."""
        self.profile_var.set(settings.get("profile", "dynamic"))
        self.sensitivity_var.set(settings.get("sensitivity", 1.0))
        self.sensitivity_label.configure(
            text=f"Mouse Speed: {settings.get('sensitivity', 1.0):.1f}x")
        self.deadzone_var.set(settings.get("deadzone", 2))
        self.deadzone_label.configure(
            text=f"Deadzone: {settings.get('deadzone', 2)} px")
        self.disable_accel_var.set(settings.get("disable_acceleration", True))
        self.accel_level_var.set(settings.get("acceleration_level", 2))
        self.accel_label.configure(
            text=f"Acceleration Curve: {self._accel_names[settings.get('acceleration_level', 2)]}")
        self.scroll_sens_var.set(settings.get("scroll_sensitivity", 4))
        self.scroll_label.configure(
            text=f"Scroll Speed: {settings.get('scroll_sensitivity', 4)}")
        self.update_ui_states()
