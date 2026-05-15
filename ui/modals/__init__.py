# SPDX-License-Identifier: GPL-3.0-or-later
"""Top-level Tk modal windows (accessibility prompt, joy-side selector).

Each modal is a function that takes the parent ``JoyglideUI`` window
plus any per-modal arguments. Implemented as functions (not mixin
methods) because they're standalone Toplevel windows with their own
lifecycle — they don't share state with the tabs.
"""
