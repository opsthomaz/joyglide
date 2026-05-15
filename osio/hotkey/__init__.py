# SPDX-License-Identifier: GPL-3.0-or-later
"""Platform dispatcher for the global pause hotkey."""
import sys

if sys.platform == "darwin":
    from osio.hotkey.macos import install_pause_hotkey
elif sys.platform == "win32":
    from osio.hotkey.windows import install_pause_hotkey
elif sys.platform.startswith("linux"):
    from osio.hotkey.linux import install_pause_hotkey
else:
    from applog import get_logger
    _log = get_logger(__name__)
    def install_pause_hotkey(callback):
        _log.warning(f"⚠️ Global hotkey not supported on {sys.platform} — skipping.")
