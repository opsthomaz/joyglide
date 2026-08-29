# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Windows build.

Builds a single-file .exe with no console window. All assets bundled.

Usage (from repo root, with deps installed):
  pyinstaller --clean packaging/joyglide_windows.spec

Output:
  dist/Joyglide.exe
"""
from pathlib import Path

block_cipher = None

# This spec lives in packaging/, but every path it references (main.py,
# assets/, the .ico) is at the repo root. SPECPATH is the directory of
# the .spec file itself, so .parent is the repo root.
PROJECT_ROOT = Path(SPECPATH).parent

# Datas to bundle inside the exe — referenced via utils.resource_path() at runtime.
datas = [
    (str(PROJECT_ROOT / 'assets'), 'assets'),
]

# Hidden imports — modules loaded via sys.platform dispatch or by libraries
# that do dynamic imports (PyInstaller doesn't see those statically).
hiddenimports = [
    # osio/ package — moved from flat root files (mouse.py / mouse_windows.py
    # / hotkey.py / hotkey_windows.py / platform_setup.py) into a layered
    # platform-dispatcher package during PR #5 of the modular blueprint.
    'osio',
    'osio.boost',
    'osio.mouse',
    'osio.mouse.windows',
    'osio.hotkey',
    'osio.hotkey.windows',
    # ble/ package — split out from main.py during the modular blueprint
    # refactor. PyInstaller's static analysis follows the imports, but
    # listing the leaf modules explicitly is belt-and-suspenders for
    # the spec → no surprise "module not found" at runtime.
    'ble',
    'ble.constants',
    'ble.feature_flags',
    'ble.protocol',
    'ble.connection',
    # parser/ + engine/ packages — split out from joycon.py during PR #2
    # of the modular blueprint. Same rationale as ble/.
    'parser',
    'parser.constants',
    'parser.button_masks',
    'parser.u16_delta',
    'parser.battery',
    'parser.buttons',
    'parser.mouse_optical',
    'parser.sticks',
    'parser.imu',
    'parser.magnetometer',
    'parser.power_info',
    'engine',
    'engine.tuning',
    'engine.motion_pump',
    # tray.py + bg_loop.py — split out from main.py during PR #3 of the
    # modular blueprint. PyInstaller's analysis follows main.py's imports,
    # but listing them keeps the spec's intent explicit.
    'tray',
    'bg_loop',
    # ui/ package — split out from ui.py (PR #4 of the modular blueprint).
    'ui',
    'ui._shared',
    'ui.dashboard',
    'ui.buttons_tab',
    'ui.performance',
    'ui.settings_tab',
    'ui.modals',
    'ui.modals.accessibility',
    'ui.modals.joy_select',
    # CustomTkinter loads tk extensions late
    'customtkinter',
    'PIL._tkinter_finder',
    # pystray picks the Windows backend at import time
    'pystray._win32',
    # bleak loads its WinRT backend dynamically
    'bleak.backends.winrt',
    'bleak.backends.winrt.client',
    'bleak.backends.winrt.scanner',
    # winrt submodule used by platform_setup.request_throughput_optimized.
    # NOT winsdk — bleak uses `winrt` and the two projections don't interop
    # (TypeError: convert_to returned null).
    'winrt.windows.devices.bluetooth',
    'winrt.windows.foundation',
]

# Modules we explicitly do NOT want to drag in.
excludes = [
    # macOS-only — would fail to import on Windows even though sys.platform
    # gates them at runtime.
    'osio.mouse.macos',
    'osio.hotkey.macos',
    'Quartz',
    'Foundation',
    'AppKit',
    'pyobjc_framework_Quartz',
    'pyobjc_framework_Cocoa',
    # We don't use these.
    'rubicon',
    'uvicorn',
    'pynput',
    # py2app is a build tool, not a runtime dep.
    'py2app',
]

a = Analysis(
    [str(PROJECT_ROOT / 'main.py')],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# Pick an icon if available — build script generates it from assets/joyglide.png
ICON_PATH = PROJECT_ROOT / 'assets' / 'joyglide.ico'

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='Joyglide',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,            # UPX often triggers AV false positives — not worth it
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,         # windowed app — no console pop-up
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON_PATH) if ICON_PATH.exists() else None,
)
