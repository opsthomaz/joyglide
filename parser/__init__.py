# SPDX-License-Identifier: GPL-3.0-or-later
"""Input report 0x05 parsers — pure functions that read bytes and mutate state.

Each submodule handles one logical slice of the report:

  * ``parser.battery``       — voltage / charge state / current (mA)
  * ``parser.buttons``       — bitmask diffing → click events
  * ``parser.mouse_optical`` — absolute X/Y deltas → accumulator
  * ``parser.sticks``        — analog stick → scroll accumulator

All take a ``state`` object (the ``JoyCon`` instance from
``engine.joycon``) and the raw report bytes. Side effects mutate the
state in place; no return values.

Constants live in ``parser.constants`` (offsets) and
``parser.button_masks`` (the JoyCon button bitfield layout).
"""
