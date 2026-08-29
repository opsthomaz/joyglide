# SPDX-License-Identifier: GPL-3.0-or-later
"""Button parser — bitmask diff → click events.

Reads the side-specific 24-bit button field, diffs against the previous
packet, and emits the mapped mouse action via the state's InputSimulator
when a mapped button transitions (``settings["button_map"]``). Other
button transitions are logged at DEBUG level only.
"""
from applog import get_logger
from parser.button_masks import MASKS
from user_preferences import DEFAULT_BUTTON_MAP, settings

log = get_logger(__name__)


# Action → (press method, release method) on the InputSimulator. Which
# button triggers which action comes from ``settings["button_map"]``
# (see ``user_preferences.DEFAULT_BUTTON_MAP`` for the default layout and
# the rationale per button); ``settings["swap_click_buttons"]`` flips
# left↔right on top of it. Buttons with no mapping (or action "none")
# fall through to the debug-only path.
_ACTION_METHODS = {
    "left":    ("mouse_down",         "mouse_up"),
    "right":   ("mouse_down_right",   "mouse_up_right"),
    "middle":  ("mouse_down_middle",  "mouse_up_middle"),
    "back":    ("mouse_down_back",    "mouse_up_back"),
    "forward": ("mouse_down_forward", "mouse_up_forward"),
}
_SWAP = {"left": "right", "right": "left"}


def _methods_for(name: str):
    """Resolve ``name`` → (press, release) method names, or None."""
    action = settings.get("button_map", DEFAULT_BUTTON_MAP).get(name)
    if action is None:
        return None
    if settings.get("swap_click_buttons", False):
        action = _SWAP.get(action, action)
    return _ACTION_METHODS.get(action)


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
            methods = _methods_for(name)
            if methods is not None:
                held[name] = methods[1]
                getattr(sim, methods[0])()
        else:
            # Prefer the release recorded at press time; fall back to the
            # current map when there is none (e.g. ``last_data`` was
            # seeded with the button already held).
            up = held.pop(name, None)
            if up is None:
                methods = _methods_for(name)
                up = methods[1] if methods is not None else None
            if up is not None:
                getattr(sim, up)()
            else:
                log.debug(name)
