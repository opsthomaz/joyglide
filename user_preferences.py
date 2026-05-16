# SPDX-License-Identifier: GPL-3.0-or-later
"""Persistent user preferences, backed by JSON in the OS data directory.

Settings live at:

  * macOS:   ``~/Library/Application Support/joyglide/settings.json``
  * Windows: ``%LOCALAPPDATA%/opsthomaz/joyglide/settings.json``
  * Linux:   ``~/.local/share/joyglide/settings.json``

(Path resolved by ``platformdirs.user_data_dir`` so it follows each OS's
convention. ``platformdirs`` is the maintained successor to the now-archived
``appdirs`` package; same call signature, identical paths.)

Module-level ``settings`` is a plain dict loaded once at import. The BLE
threads read from it on every packet (~33-67 Hz); the Tk thread writes to
it on UI interaction. No lock is needed — Python's GIL protects dict
get/set at the bytecode level, and a momentarily stale value is harmless
for these tuning knobs.

To add a new setting, add it to ``DEFAULTS`` (so existing config files get
migrated automatically on next load). To retire one, list it in
``LEGACY_KEYS`` so it gets stripped on load.
"""
import json
from pathlib import Path
from platformdirs import user_data_dir

APP_NAME = "joyglide"
APP_AUTHOR = "opsthomaz"  # platformdirs path segment (Windows); not license attribution


# Default values for every setting we know about. Acts as both the initial
# config when settings.json doesn't exist yet and as a migration source
# (any key missing from a loaded settings.json gets its default value).
DEFAULTS = {
    # ── Startup ─────────────────────────────────────────────────────────
    "ignore_opening_window": False,   # start minimised to tray instead of showing the window
    "start_with_sync": False,         # auto-start a BLE scan as soon as the app boots

    # ── Motion ──────────────────────────────────────────────────────────
    "profile": "dynamic",             # "dynamic" / "gaming" / "cinematic" — see joycon.py
    "sensitivity": 1.0,               # global multiplier (0.5–3.0)
    "deadzone": 2,                    # raw optical-sensor units; 0 in Gaming regardless

    # ── Acceleration ────────────────────────────────────────────────────
    "disable_acceleration": True,     # force 1:1 (overrides Dynamic's smart curve)
    "acceleration_level": 2,          # 1=Low, 2=Med, 3=High — applies in Dynamic only

    # ── Scroll (analog stick) ───────────────────────────────────────────
    "scroll_sensitivity": 4,          # 1–10, normalised so 4 = 1.0× baseline

    # ── Input ───────────────────────────────────────────────────────────
    "double_click_enabled": True,     # rapid press within 400ms = double-click

    # ── Hardware ────────────────────────────────────────────────────────
    "vibration_on_connect": True,     # play the PAIRING haptic when a JC2 connects
    "show_gatt_dump": False,          # log full GATT profile on connect (debug aid)

    # ── IMU (gyro + accel) ─────────────────────────────────────────────
    # Off by default — when on, adds FEATURE_IMU (bit 2) to the feature
    # mask so the controller emits the 18-byte Motion Data block at
    # offset 0x2A of input report 0x05 (cost: a small bit of controller
    # battery for the active sensor). Enable to power air-mouse mode or
    # to dump raw IMU values for protocol verification.
    "imu_enabled":  False,
    "imu_dump_raw": False,            # log decoded IMU values per packet (diagnostic)

    # ── Magnetometer ────────────────────────────────────────────────────
    # Off by default — when on, parser.magnetometer decodes the 6-byte
    # block at offset 0x19 of input report 0x05 and stashes the raw
    # (x, y, z) tuple on state. The FEATURE_MAGNETOMETER bit (0x80)
    # is already in the default mask 0xFF, so the controller is
    # already emitting the bytes; this setting just gates whether
    # we *parse* them on every packet. Toggle to enable for future
    # air-mouse / orientation features.
    "magnetometer_enabled":  False,
    "magnetometer_dump_raw": False,   # log raw mag values per packet (diagnostic)

    # ── Battery diagnostics ─────────────────────────────────────────────
    # Off by default — when on, parser/battery.py logs a one-liner per
    # 1 Hz tick with mv/pct/mA for the battery fields. The current field
    # at offset 0x22 is hardware-verified: raw u16 / 100 = mA (Tier S).
    "battery_log": False,

    # ── Motion prediction (between-BLE-packet smoothing) ───────────────
    # Off by default — when on, the pump extrapolates a small synthetic
    # delta on ticks where no fresh BLE packet has arrived since the
    # last tick, using the previous BLE-frame velocity. Smooths visible
    # cursor stepping when display refresh > BLE rate (always true).
    "motion_prediction_enabled": False,

    # ── Internal (managed by the app, not a user-facing setting) ────────
    "devices": {}                     # address → {"type": "left"/"right"} memory
}


# Settings that used to exist but no longer do. They get stripped on load
# so old settings.json files don't carry dead keys forever.
LEGACY_KEYS = {"use_delta_time_pump", "use_raw_mode"}


def get_settings_path() -> "Path":
    """Resolve the absolute path to ``settings.json`` and ensure its parent
    directory exists. The directory location follows the OS convention
    via ``platformdirs.user_data_dir``."""
    settings_dir = Path(user_data_dir(APP_NAME, APP_AUTHOR))
    settings_dir.mkdir(parents=True, exist_ok=True)
    return settings_dir / "settings.json"


def load_settings() -> dict:
    """Load the persisted settings dict, applying migrations on the fly.

    Missing keys present in ``DEFAULTS`` are filled in (so users from
    older versions don't see ``KeyError`` after upgrade). Keys listed in
    ``LEGACY_KEYS`` are stripped (so retired settings don't accumulate
    forever). The resulting dict is written back to disk so the migration
    is durable.

    On first run (no settings file yet), the defaults are written and
    the function recurses to load them — a single round-trip.
    """
    settings_file = get_settings_path()
    if settings_file.exists():
        with settings_file.open() as f:
            settings = json.load(f)
        for key, default in DEFAULTS.items():
            if key not in settings:
                settings[key] = default
        for key in LEGACY_KEYS:
            settings.pop(key, None)
        save_settings(settings)
        return settings
    create_default_settings()
    return load_settings()


def save_settings(settings: dict) -> None:
    """Atomically replace the settings file with the given dict, indented
    for human readability. Caller is responsible for not invoking from
    the AppKit/Tk main thread (use ``bg_loop.save_settings_async`` to
    push the write to a daemon thread).

    Atomicity is achieved by writing to a ``.tmp`` sibling on the same
    filesystem and then ``Path.replace`` — POSIX guarantees the rename
    is atomic, so any crash / power loss mid-write leaves either the
    old file untouched or the new one fully in place, never a half-
    written JSON that would crash ``load_settings`` next launch.
    """
    settings_file = get_settings_path()
    tmp = settings_file.with_suffix(".tmp")
    with tmp.open("w") as f:
        json.dump(settings, f, indent=2)
    tmp.replace(settings_file)


def create_default_settings() -> None:
    """Write a fresh settings file populated with the defaults. Called
    on first run when no settings file exists yet."""
    settings_file = get_settings_path()
    with settings_file.open("w") as f:
        json.dump(DEFAULTS.copy(), f, indent=2)


if __name__ == "__main__":
    print(load_settings())
else:
    settings = load_settings()
