# SPDX-License-Identifier: GPL-3.0-or-later
"""Motion engine — converts the 33Hz/67Hz BLE packet stream into smooth
per-frame cursor movement at the display refresh rate.

Submodules:
  * ``engine.tuning``      — pump tuning constants (drain, idle brake, max delta).
  * ``engine.motion_pump`` — the asyncio task that runs at display Hz and
                             drains the per-axis accumulator into actual
                             cursor moves.

The ``JoyCon`` class itself stays in ``joycon.py`` at the project root for
import-stability — it composes the parsers (``parser.*``) and this engine
(``engine.motion_pump``).
"""
