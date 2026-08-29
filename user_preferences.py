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


# Button name (parser/button_masks.py) → mouse action. Buttons absent from
# this map (B, X, PLUS, MINUS, HOME, CHAT, SHARE, SL, SR, UP, DOWN) are not
# remappable and do nothing. Actions: left / right / middle / back /
# forward / none. ``swap_click_buttons`` flips left↔right on top of it.
DEFAULT_BUTTON_MAP = {
    "L":     "left",
    "R":     "left",
    "ZL":    "right",
    "ZR":    "right",
    "STICK": "middle",
    "A":     "forward",
    "Y":     "back",
    "LEFT":  "back",
    "RIGHT": "forward",
}
BUTTON_ACTIONS = ("left", "right", "middle", "back", "forward", "none")

# Buttons that live on the same physical Joy-Con (STICK exists on both
# and is not counted). Used by the Buttons tab to warn when a side has
# no click at all after an Apply.
BUTTON_SIDES = {"left": ("L", "ZL", "LEFT", "RIGHT"), "right": ("R", "ZR", "A", "Y")}


def sides_without_click(button_map: dict) -> list[str]:
    """Names of Joy-Con sides whose buttons include neither a left nor a
    right click. Empty list means every side can still click."""
    return [side for side, buttons in BUTTON_SIDES.items()
            if not any(button_map.get(b) in ("left", "right") for b in buttons)]


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
    "swap_click_buttons": False,      # False: shoulder L/R = left click, trigger ZL/ZR = right
                                      # True:  trigger = left click, shoulder = right
    "button_map": dict(DEFAULT_BUTTON_MAP),  # button → action, see DEFAULT_BUTTON_MAP

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

    # ── Latency tracing (developer / verification tool) ─────────────────
    # Off by default — zero runtime cost when off (one bool check on the
    # hot path). When on, latency_trace.py captures perf_counter_ns
    # timestamps around CGEventPost and emits aggregated p50/p95/max
    # per second via applog. Used to quantify the BLE → CGEventPost
    # budget. See latency_trace.py module docstring.
    "latency_trace": False,

    # ── Internal (managed by the app, not a user-facing setting) ────────
    "devices": {}                     # address → {"type": "left"/"right"} memory
}


# Settings that used to exist but no longer do. They get stripped on load
# so old settings.json files don't carry dead keys forever.
LEGACY_KEYS = {"use_delta_time_pump", "use_raw_mode"}


# Value-level validation specs (see validate_settings). Ranges are
# inclusive; ``None`` upper bound means unbounded above. Documented ranges
# mirror the comments in DEFAULTS and the UI controls.
_NUMERIC_RANGES = {
    "sensitivity":        (0.5, 3.0),   # global multiplier
    "deadzone":           (0, None),    # raw optical units, >= 0
    "acceleration_level": (1, 3),       # 1=Low, 2=Med, 3=High
    "scroll_sensitivity": (1, 10),      # normalised so 4 = 1.0x
}
_INT_SETTING_KEYS = {"deadzone", "acceleration_level", "scroll_sensitivity"}
_VALID_PROFILES = {"dynamic", "gaming", "cinematic"}


def validate_settings(settings: dict) -> dict:
    """Coerce out-of-range or wrong-typed values to safe defaults.

    Users are told to hand-edit ``settings.json``, so a corrupt or typo'd
    value (``"sensitivity": "fast"``, ``"acceleration_level": 99``) must not
    reach the hot-path parsers. Numeric values clamp to their documented
    range (and int-typed keys coerce to ``int``); wrong-typed booleans,
    dicts, the ``profile`` enum, and non-numeric numerics reset to the
    ``DEFAULTS`` value. Mutates and returns ``settings``.
    """
    # Type guard for bool / dict keys against DEFAULTS. (bool is an int
    # subclass, so an int given for a bool key still resets — intentional.)
    for key, default in DEFAULTS.items():
        if key not in settings:
            continue
        if (isinstance(default, bool) and not isinstance(settings[key], bool)) or \
           (isinstance(default, dict) and not isinstance(settings[key], dict)):
            settings[key] = default

    if settings.get("profile") not in _VALID_PROFILES:
        settings["profile"] = DEFAULTS["profile"]

    # button_map: exactly the mappable buttons, each with a valid action.
    # Unknown buttons are dropped; missing / invalid ones get the default.
    raw_map = settings.get("button_map")
    if not isinstance(raw_map, dict):
        raw_map = {}
    settings["button_map"] = {
        btn: (raw_map[btn] if raw_map.get(btn) in BUTTON_ACTIONS else default_action)
        for btn, default_action in DEFAULT_BUTTON_MAP.items()
    }

    for key, (lo, hi) in _NUMERIC_RANGES.items():
        val = settings.get(key)
        # Exclude bool (int subclass) — a True given for a numeric is junk.
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            settings[key] = DEFAULTS[key]
            continue
        val = max(lo, val)
        if hi is not None:
            val = min(hi, val)
        settings[key] = int(val) if key in _INT_SETTING_KEYS else val

    return settings


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
        try:
            with settings_file.open() as f:
                settings = json.load(f)
        except json.JSONDecodeError:
            # Hand-edited file with a syntax error. Keep the user's file
            # for inspection and start from defaults rather than dying at
            # import time with no window to explain why.
            settings_file.replace(settings_file.with_suffix(".json.corrupt"))
            create_default_settings()
            return load_settings()
        for key, default in DEFAULTS.items():
            if key not in settings:
                settings[key] = default
        for key in LEGACY_KEYS:
            settings.pop(key, None)
        validate_settings(settings)
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
