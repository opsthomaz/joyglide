# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the macOS .app build.

py2app 0.28.10 doesn't support Python 3.12+ cleanly (zlib.__file__ removed
upstream — known issue), so we use PyInstaller for both OSes. Same tool,
same lifecycle, parallel spec files.

Usage (from repo root, in venv):
  pyinstaller --clean --noconfirm packaging/joyglide_macos.spec

Output:
  dist/Joyglide.app
"""
from pathlib import Path

block_cipher = None

# This spec lives in packaging/, but every path it references (main.py,
# assets/, Joyglide.icns) is at the repo root. SPECPATH is the directory
# of the .spec file itself, so .parent is the repo root.
PROJECT_ROOT = Path(SPECPATH).parent

datas = [
    (str(PROJECT_ROOT / 'assets'), 'assets'),
]

# Hidden imports — modules loaded via sys.platform dispatch or by libs that
# import dynamically. PyInstaller's static analysis can miss these.
hiddenimports = [
    # osio/ package — moved from flat root files (mouse.py / mouse_macos.py
    # / hotkey.py / hotkey_macos.py / platform_setup.py) into a layered
    # platform-dispatcher package during PR #5 of the modular blueprint.
    'osio',
    'osio.boost',
    'osio.mouse',
    'osio.mouse.macos',
    'osio.hotkey',
    'osio.hotkey.macos',
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
    # The mixin classes are imported by ui/__init__.py but listing leaves
    # explicitly avoids any PyInstaller static-analysis surprise.
    'ui',
    'ui._shared',
    'ui.dashboard',
    'ui.performance',
    'ui.settings_tab',
    'ui.modals',
    'ui.modals.accessibility',
    'ui.modals.joy_select',
    'customtkinter',
    'PIL._tkinter_finder',
    # pystray's macOS backend
    'pystray._darwin',
    'pystray._util',
    # NOTE: pystray._util.darwin does NOT exist (only gtk/win32/notify_dbus
    # in pystray._util). The Darwin code lives directly in pystray._darwin
    # — verified by inspecting site-packages/pystray/_util/. Earlier specs
    # listed _util.darwin but PyInstaller would emit a warning each build.
    # bleak's CoreBluetooth backend
    'bleak.backends.corebluetooth',
    'bleak.backends.corebluetooth.client',
    'bleak.backends.corebluetooth.scanner',
    # pyobjc submodules used at runtime
    'Quartz',
    'Quartz.CoreGraphics',
    'Foundation',
    'AppKit',
    # ApplicationServices is loaded lazily inside mouse_macos.request_accessibility
    # for the AXIsProcessTrustedWithOptions prompt — PyInstaller static analysis
    # misses it otherwise.
    'ApplicationServices',
]

excludes = [
    # Windows-only siblings — fail to import on macOS
    'osio.mouse.windows',
    'osio.hotkey.windows',
    'winrt',
    # Unused legacy
    'rubicon',
    'uvicorn',
    'pynput',
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
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

ICON_PATH = PROJECT_ROOT / 'Joyglide.icns'

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Joyglide',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    # arm64 native (Apple Silicon). Intel Macs run via Rosetta 2 (transparent,
    # macOS prompts to install on first launch). A true universal2 build would
    # need every wheel dep installed with --platform macosx_*_universal2 which
    # is non-trivial to wire in CI; sticking with arm64 native keeps the build
    # simple and Rosetta covers Intel transparently.
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON_PATH) if ICON_PATH.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Joyglide',
)

# Wrap as .app bundle (macOS-specific BUNDLE step)
app = BUNDLE(
    coll,
    name='Joyglide.app',
    icon=str(ICON_PATH) if ICON_PATH.exists() else None,
    bundle_identifier='com.opsthomaz.joyglide',
    info_plist={
        'CFBundleName':                'Joyglide',
        'CFBundleDisplayName':         'Joyglide',
        'CFBundleVersion':             '0.1.0',
        'CFBundleShortVersionString':  '0.1.0',
        'CFBundleIdentifier':          'com.opsthomaz.joyglide',
        'CFBundlePackageType':         'APPL',
        # LSUIElement = 1 → menu-bar app, no Dock icon
        'LSUIElement':                 True,
        # Required entitlements for cursor injection
        'NSAccessibilityUsageDescription':
            'Joyglide needs accessibility access to control the mouse.',
        # Required for BLE scanning
        'NSBluetoothAlwaysUsageDescription':
            'Joyglide needs Bluetooth to connect to your Joy-Con 2.',
        'NSBluetoothPeripheralUsageDescription':
            'Joyglide needs Bluetooth to connect to your Joy-Con 2.',
        # Minimum macOS version. We target Catalina (10.15) — the oldest
        # macOS that supports modern CoreBluetooth, NSActivity, and the
        # CGEvent APIs we use. In practice this covers every Mac shipped
        # since ~2012 (10.15 dropped 32-bit support but kept Macs from 2012+).
        'LSMinimumSystemVersion':      '10.15',
        # High-DPI rendering
        'NSHighResolutionCapable':     True,
    },
)
