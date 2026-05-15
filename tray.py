# SPDX-License-Identifier: GPL-3.0-or-later
"""System-tray icon + right-click menu construction.

Encapsulates everything pystray-related so ``main.py`` doesn't have to
deal with menu wiring directly. Menu callbacks that mutate settings
(profile / acceleration toggles) are self-contained here; callbacks that
need application state (player list, vibration playback) are injected
by the caller via ``create_icon`` keyword arguments.

The macOS-specific care taken in tray callbacks (defer all Tk and disk
I/O so the AppKit menu callback returns immediately) is documented inline
where it matters.
"""
from collections.abc import Callable

from PIL import Image
from pystray import Icon, Menu, MenuItem

from bg_loop import save_settings_async
from user_preferences import settings
from utils import resource_path


# Documented vibration sample IDs (per ndeadly's reverse-engineering of the
# official Switch protocol). Most are short (<1s); 0x01 is the longest and
# most clearly a haptic buzz, 0x05 is a stronger click.
VIBRATION_PRESETS: list[tuple[int, str]] = [
    (0x01, "0x01 — low-frequency buzz, ~1s"),
    (0x02, "0x02 — high-freq buzz + beep-beep alarm (\"search controllers\")"),
    (0x03, "0x03 — soft click-click (\"connection\")"),
    (0x04, "0x04 — high-to-higher beep-beep (low-battery alarm)"),
    (0x05, "0x05 — like 0x03 but stronger"),
    (0x06, "0x06 — short, high-freq beep"),
    (0x07, "0x07 — short, higher-freq beep"),
]


def create_icon(
    *,
    command_queue,
    on_sync_new: Callable[[], None],
    on_emit_sound: Callable[[], None],
    on_play_vibration: Callable[[int], "object"],
    on_quit: Callable[[], None],
):  # returns pystray.Icon — annotation omitted because pystray defines
    # `Icon = backend().Icon` dynamically; pyright treats the bound name as a
    # value rather than a type.
    """Build the pystray Icon with its full menu wired up.

    Dependencies are passed in by keyword to keep this module free of
    circular imports back to ``main.py``:
      * ``command_queue``    — UI event channel; menu callbacks post
                               settings/show-dashboard events here.
      * ``on_sync_new``      — invoked when the user clicks "Sync new
                               Controller". Caller schedules the BLE flow.
      * ``on_emit_sound``    — invoked by "Say hi"; caller plays the
                               canonical haptic on every connected JC.
      * ``on_play_vibration``— invoked by each "Test Vibration Preset"
                               item with the preset ID; caller schedules
                               the actual playback (returns a Future-like).
      * ``on_quit``          — clean-shutdown callback for the Exit item.
    """
    image = Image.open(resource_path("assets/joyglide.png"))

    # ── Main menu items ─────────────────────────────────────────────────
    def _show_dashboard(_icon, _item) -> None:
        # Don't call Tk from here. This fires on the AppKit main thread
        # (which is also the Tk mainloop thread); a direct deiconify/lift
        # hands the menu callback a long synchronous Tkapp_Call before
        # AppKit gets the main thread back. Hand it to the queue instead —
        # process_queue picks it up on Tk's next 100ms tick.
        command_queue.put({"type": "show_dashboard"})

    def _on_sync(_icon, _item) -> None:
        on_sync_new()

    def _on_emit_sound(_icon, _item) -> None:
        on_emit_sound()

    def _on_quit(_icon, _item) -> None:
        on_quit()

    open_dash           = MenuItem('Open Dashboard',     _show_dashboard, default=True)
    sync_new_controller = MenuItem('Sync new Controller', _on_sync)

    # ── Motion Profile submenu ─────────────────────────────────────────
    def _set_profile(profile_name: str):
        def callback(_icon, _item) -> None:
            settings["profile"] = profile_name
            save_settings_async()
            command_queue.put({"type": "update_settings"})
        return callback

    def _is_profile(profile_name: str):
        return lambda _item: settings.get("profile", "dynamic") == profile_name

    def _toggle_acceleration(_icon, _item) -> None:
        settings["disable_acceleration"] = not settings.get("disable_acceleration", True)
        save_settings_async()
        command_queue.put({"type": "update_settings"})

    disable_accel_item = MenuItem(
        'Disable Acceleration (Force Raw)',
        _toggle_acceleration,
        checked=lambda _item: settings.get("disable_acceleration", True),
    )

    def _set_accel_level(level: int):
        def callback(_icon, _item) -> None:
            settings["acceleration_level"] = level
            save_settings_async()
            command_queue.put({"type": "update_settings"})
        return callback

    def _is_accel_level(level: int):
        return lambda _item: settings.get("acceleration_level", 2) == level

    accel_level_menu = MenuItem('Acceleration Level', Menu(
        MenuItem('Low (1.5x max)',    _set_accel_level(1), checked=_is_accel_level(1), radio=True),
        MenuItem('Medium (2.5x max)', _set_accel_level(2), checked=_is_accel_level(2), radio=True),
        MenuItem('High (3.5x max)',   _set_accel_level(3), checked=_is_accel_level(3), radio=True),
    ))

    profiles_menu = MenuItem('Motion Profile', Menu(
        MenuItem('🚀 Dynamic (Smart & Smooth)',     _set_profile('dynamic'),   checked=_is_profile('dynamic'),   radio=True),
        MenuItem('🎯 Gaming / FPS (Raw 1:1)',       _set_profile('gaming'),    checked=_is_profile('gaming'),    radio=True),
        MenuItem('🍿 Cinematic (Couch & Relaxed)',  _set_profile('cinematic'), checked=_is_profile('cinematic'), radio=True),
        Menu.SEPARATOR,
        disable_accel_item,
        accel_level_menu,
    ))

    # ── Debug submenu ──────────────────────────────────────────────────
    debug_emit_sound = MenuItem('Say hi', _on_emit_sound)

    # Vibration test submenu — fires each documented preset so users can
    # tell which feels like what on their controller. Useful when
    # diagnosing "say hi doesn't vibrate" reports (preset 0x04 is a faint
    # beep, not a buzz).
    def _make_vib_test(pid: int):
        def fire(_icon, _item) -> None:
            on_play_vibration(pid)
        return fire

    vib_test_items = [MenuItem(label, _make_vib_test(pid)) for pid, label in VIBRATION_PRESETS]
    vibration_test_menu = MenuItem('Test Vibration Preset', Menu(*vib_test_items))

    debug_menu = MenuItem('DEBUG', Menu(debug_emit_sound, vibration_test_menu))

    # ── Final assembled menu ───────────────────────────────────────────
    menu = Menu(
        open_dash,
        sync_new_controller,
        profiles_menu,
        debug_menu,
        MenuItem('Exit', _on_quit),
    )

    return Icon("joyglide", image, menu=menu)
