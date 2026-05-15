# SPDX-License-Identifier: GPL-3.0-or-later
"""macOS build via py2app.

Usage:
  python setup.py py2app
or via the wrapper:
  ./build.sh

Output: dist/Joyglide.app
"""
from setuptools import setup

APP = ['main.py']
DATA_FILES: list[str] = []
OPTIONS = {
    'argv_emulation': False,
    # Bundles required for runtime — py2app misses these without help.
    'packages': [
        'PIL',
        'pystray',
        'tkinter',
        'customtkinter',
        'bleak',
        'appdirs',
    ],
    # Hidden imports — modules referenced via sys.platform dispatch
    # or by libraries that import dynamically.
    'includes': [
        'mouse_macos',
        'hotkey_macos',
        'platform_setup',
        'Quartz',
        'Foundation',
    ],
    'iconfile': 'Joyglide.icns',
    'plist': 'Info.plist',
    'excludes': [
        'rubicon',
        'uvicorn',
        'pynput',
        # Windows-only siblings — would fail to import on macOS.
        'mouse_windows',
        'hotkey_windows',
    ],
    'resources': ['assets'],
}

setup(
    name='Joyglide',
    app=APP,
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
    # py2app 0.28+ rejects install_requires (raises "no longer supported").
    # Setuptools auto-populates this from pyproject.toml's [project.dependencies]
    # — we keep pyproject.toml metadata-only for that reason. Pin to empty
    # explicitly here as a belt-and-suspenders guard.
    install_requires=[],
)
