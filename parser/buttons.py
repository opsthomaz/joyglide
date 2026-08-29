# SPDX-License-Identifier: GPL-3.0-or-later
"""Button parser — bitmask diff → click events.

Reads the side-specific 24-bit button field, diffs against the previous
packet, and emits mouse_down / mouse_up via the state's InputSimulator
when the L/R or ZL/ZR buttons transition. Other button transitions
are logged at DEBUG level only.
"""
from applog import get_logger
from parser.button_masks import MASKS
from user_preferences import settings

log = get_logger(__name__)


# Button-name → (press method, release method) on the InputSimulator.
# Lookup-table dispatch keeps complexity flat as new mappings are added
# (xenon's complexity gate; "if name in ..." chains add a rank per
# branch). Names not in this table fall through to the debug-only path.
_MOUSE_METHODS = {
    "R":     ("mouse_down",         "mouse_up"),
    "L":     ("mouse_down",         "mouse_up"),
    "ZR":    ("mouse_down_right",   "mouse_up_right"),
    "ZL":    ("mouse_down_right",   "mouse_up_right"),
    # Pressing the analog stick acts as middle-mouse click — same role
    # as a wheel-button on a regular mouse. Same name on both sides per
    # parser/button_masks.py.
    "STICK": ("mouse_down_middle",  "mouse_up_middle"),
    # Right-side face buttons → browser / Finder navigation.
    # A and Y only exist in the right Joy-Con's mask, so these
    # entries are no-ops on a left controller. Standard convention:
    # button 4/X1 = back, button 5/X2 = forward.
    "A":     ("mouse_down_forward", "mouse_up_forward"),
    "Y":     ("mouse_down_back",    "mouse_up_back"),
    # Left-side D-pad → browser / Finder navigation. RIGHT (forward)
    # and LEFT (back) chosen because they're spatially intuitive —
    # press D-pad-right to go forward, D-pad-left to go back. UP/DOWN
    # left unbound (could be scroll wheel or PgUp/PgDn in a future
    # revision; not worth choosing a default). LEFT and RIGHT only
    # exist in the left Joy-Con's mask, so these entries are no-ops
    # on a right controller.
    "LEFT":  ("mouse_down_back",    "mouse_up_back"),
    "RIGHT": ("mouse_down_forward", "mouse_up_forward"),
}

# ``settings["swap_click_buttons"]`` layout: trigger = left click,
# shoulder = right click. Everything else is identical.
_MOUSE_METHODS_SWAPPED = {
    **_MOUSE_METHODS,
    "R":  ("mouse_down_right", "mouse_up_right"),
    "L":  ("mouse_down_right", "mouse_up_right"),
    "ZR": ("mouse_down",       "mouse_up"),
    "ZL": ("mouse_down",       "mouse_up"),
}


def parse(state, data: bytes) -> None:
    """Diff button state from previous packet, emit click events."""
    # While paused, freeze button-state tracking entirely — don't update
    # last_data. This avoids a subtle bug: if the user clicks during pause
    # and releases after unpause, advancing last_data through pause would
    # produce an unmatched mouse_up (release transition) without a prior
    # mouse_down. By keeping last_data frozen, the post-unpause packet
    # compares against the pre-pause state, so any button still held when
    # we resume is treated as the resting state — no spurious events.
    if state.paused:
        return

    button_map  = MASKS[state.side]
    offset      = 4 if state.is_left else 3
    # Runt-packet guard — without this, ``data[offset:offset + 3]`` would
    # silently return a short slice and ``int.from_bytes`` would decode it
    # as a smaller magnitude, producing a spurious bitmask diff that
    # synthesises phantom press/release events. Matches the bounds-check
    # style in ``parser/battery.py``.
    if len(data) < offset + 3:
        return
    cur_state   = int.from_bytes(data[offset:offset + 3], 'big')
    last_state  = int.from_bytes(state.last_data[offset:offset + 3], 'big') if state.last_data else 0
    state.last_data = data

    if cur_state == last_state:
        return

    sim = state.input_simulator
    table = _MOUSE_METHODS_SWAPPED if settings.get("swap_click_buttons", False) else _MOUSE_METHODS
    # ``state._held_ups`` remembers, per held button, the release method
    # that pairs with the press we actually fired — so toggling the swap
    # setting mid-press still releases the right mouse button instead of
    # stranding one in the "down" state.
    held = state._held_ups
    for name, mask in button_map.items():
        pressed      = bool(cur_state & mask)
        last_pressed = bool(last_state & mask)
        if pressed == last_pressed:
            continue

        if pressed:
            methods = table.get(name)
            if methods is not None:
                held[name] = methods[1]
                getattr(sim, methods[0])()
        else:
            # Prefer the release recorded at press time; fall back to the
            # current table when there is none (e.g. ``last_data`` was
            # seeded with the button already held).
            up = held.pop(name, None)
            if up is None:
                methods = table.get(name)
                up = methods[1] if methods is not None else None
            if up is not None:
                getattr(sim, up)()
            else:
                log.debug(name)
