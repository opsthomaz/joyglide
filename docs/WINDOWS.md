# Joyglide — Windows guide

## Why Windows is interesting

| Platform | LL connection interval | JC2 effective rate |
|---|---|---|
| Windows + `BluetoothLEPreferredConnectionParameters.ThroughputOptimized` | **15ms** | **~67Hz** |
| macOS | 30ms | 33Hz |
| Windows default (no app intervention) | 60ms | ~16Hz |

The Windows port roughly **doubles** the rate compared to macOS, dropping cursor input lag noticeably (~30ms → ~15ms typical perceived). It does not reach the Switch console's native 5ms / 200Hz — that requires a vendor-specific HCI command Windows doesn't expose, only Linux does.

The fast path is enabled automatically by `osio.boost.request_throughput_optimized()` after each BLE connect. No user setting needed.

> **Honesty note:** earlier docs in this repo claimed "7.5ms / 133Hz" on Windows. That came from optimistic comments in two reference projects ([TheFrano/joycon2cpp](research/thefrano_joycon2cpp/) and [Misaka10571/joycon2-connector](research/misaka_joycon2_connector/)). Both projects use the same `ThroughputOptimized` preset we use, and that preset is hard-coded to **min = max = 15ms** (verified by runtime inspection on Windows 11). There is no public WinRT constructor for arbitrary connection-parameter values, so 15ms is the practical ceiling on this API.

## Prerequisites

1. **Python 3.13 or newer** from [python.org](https://www.python.org/downloads/) — check "Add Python to PATH" during install. We test on 3.14 (latest stable); 3.13 also works.
2. **Bluetooth adapter** that supports BLE 4.0+ (built-in on essentially every laptop made after 2014).
3. **`uv`** (optional but recommended) — `pip install uv` or `winget install astral-sh.uv`.

## Setup (PowerShell, in the project folder)

With `uv` (matches the macOS workflow):

```powershell
uv venv
uv sync
```

Without `uv`:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

`requirements.txt` is the canonical list — pyproject.toml, the build script, and the CI workflow all read from it.

## Run

```powershell
.\.venv\Scripts\python.exe main.py
```

First-run sequence:

1. Tray icon appears.
2. Click **Sync New Controller**.
3. On the JC2, hold the Sync button for ~2s until the LEDs march.
4. The controller pairs and the cursor starts moving.

## Confirming BLE rate is adequate

With the `ThroughputOptimized` connection preset applied, the BLE input
rate should climb from the Windows default of ~16-33 Hz (depends on
Windows version & BT driver) to ~50-67 Hz. The app does not expose a
packet-rate readout, so judge by cursor feel: smooth, low-lag movement
indicates the boost is active; noticeably choppy ~16 Hz motion means it
is not.

If the cursor feels closer to ~16-33 Hz, something is blocking the API. Check:

- The BT driver — ancient Realtek/CSR drivers may not honor the API. Update via Windows Update or vendor site.
- Running as admin doesn't help — the API is per-app, not privileged.

## Differences from the macOS build

| Concern | macOS | Windows |
|---|---|---|
| Cursor injection | `Quartz.CGEventPost` (`osio/mouse/macos.py`) | Win32 `SendInput` via ctypes (`osio/mouse/windows.py`) |
| Pause hotkey | `CGEventTap` ⌃⌥M (`osio/hotkey/macos.py`) | `RegisterHotKey` Ctrl+Alt+M (`osio/hotkey/windows.py`) |
| Process priority | `NSProcessInfo` activity (LatencyCritical) + USER_INTERACTIVE thread QoS | `SetPriorityClass(HIGH_PRIORITY_CLASS)` + `SetThreadExecutionState` |
| BLE rate boost | n/a (macOS doesn't honor any API) | `BluetoothLEPreferredConnectionParameters.ThroughputOptimized` |
| Sub-pixel cursor | Native (Quartz accepts floats) | Software-accumulated (SendInput is integer-only) |
| Accessibility prompt | First run requests permission | Not needed |

## Packaging a `.exe`

Three ways, ordered by convenience:

### Option A — pre-built release (zero setup)

If the project has GitHub Releases, just download `Joyglide.exe` from the latest release. The `.github/workflows/build.yml` workflow builds it automatically on every tag push (`v0.1.0`, `v0.2.0`, etc.).

### Option B — local build script

Open PowerShell in the project folder:

```powershell
.\packaging\build_windows.ps1
```

The script:
1. Detects Python 3.13 / 3.14 (prefers `py -3.14`, falls back to `py -3.13`).
2. Creates a `.venv-win` venv if absent.
3. Installs everything in `requirements.txt` (bleak, pillow, pystray, appdirs, customtkinter, pyinstaller). The WinRT bindings come transitively from bleak's `winrt-Windows.*` deps — no separate `winsdk` install (we deliberately avoid it; mixing `winsdk` with bleak's `winrt` causes `TypeError: convert_to returned null` when calling `request_preferred_connection_parameters`).
4. Generates `assets\joyglide.ico` from the PNG (Pillow).
5. Runs PyInstaller with `packaging\joyglide_windows.spec`.

Output: `dist\Joyglide.exe` (one-file, no console, ~30 MB).

If you see *"convert_to returned null"* at runtime: a stale `winsdk` install is shadowing things. `pip uninstall winsdk` and try again.

### Option C — GitHub Actions (automated for forks)

The repo includes `.github/workflows/build.yml` which on every push to `main` or tag `v*` builds:
- Windows: `dist/Joyglide.exe` (PyInstaller, single-file)
- macOS:  `dist/Joyglide-macos.zip` (PyInstaller .app bundled)

Both are uploaded as Actions artifacts. Tag pushes (`git tag v0.x.x && git push --tags`) additionally attach them to a GitHub Release automatically.

## Recommended Windows settings tweaks

For best feel, **turn off "Enhance pointer precision"**:

> Settings → Bluetooth & devices → Mouse → Additional mouse settings → Pointer Options tab → uncheck **"Enhance pointer precision"**.

Why: `SendInput` with relative motion goes through Windows' built-in mouse ballistics curve (the OS's own acceleration). Our app already applies its own profile-based acceleration in `joycon.py:process_mouse`. With both layers active, the curves multiply non-linearly and the cursor feels imprecise. Turning off the OS layer makes our profile (Dynamic / Gaming / Cinematic) the only acceleration in play — same feel as on macOS (which doesn't have an OS-level acceleration curve to interfere).

This is a system-wide setting and affects all your mice. If you prefer to keep it on for normal mouse use, you can use Gaming profile in this app (which sets multiplier = 1.0 in `process_mouse`, mostly cancelling out the layering issue at the cost of disabling our smart curves).

## Known limitations on Windows

- **BT adapter age:** very old (pre-Bluetooth 4.0) adapters won't work at all. This is hardware, not software.
- **Anti-cheat games** may flag synthetic mouse input from `SendInput`. Same as any auto-clicker / macro tool. Not relevant for desktop use.
- **Display refresh rate detection** picks the **maximum** rate across all attached displays via `EnumDisplayDevicesW` + `EnumDisplaySettingsW`. So a 60Hz primary + 120Hz secondary still gives a 120Hz pump. Wasted ticks on the slow panel are cheap; missed frames on the fast one would be visible stutter, hence MAX.
- **Sub-pixel motion** is approximated by accumulating fractional deltas (Win32 `SendInput` is integer-only). At 67Hz BLE rate the per-frame deltas are small enough that integer truncation is imperceptible.
- **System timer resolution** is raised to 1ms via `timeBeginPeriod(1)` in `osio/boost.py:_boost_windows`. This is what lets `asyncio.sleep` time out reliably at 16.67ms intervals (60Hz pump). Default Windows timer is 15.6ms, which would cause routine tick slips. Trade-off: slightly higher idle CPU and battery consumption while the app runs. If you're on battery and want to save power, close the app when not in use.

## Credits

Cross-OS validation traced from two reference projects (cloned into `research/`):

- [TheFrano/joycon2cpp](https://github.com/TheFrano/joycon2cpp) — full C++ Windows app emulating Xbox via ViGEm. Confirmed `RequestPreferredConnectionParameters(ThroughputOptimized)` pattern across 4 controller types.
- [Misaka10571/joycon2-connector](https://github.com/Misaka10571/joycon2-connector) — comment in `DeviceManager.h:185` claims *"Request shortest connection interval (7.5ms) for minimal input lag"*. The real preset value is 15ms / ~67Hz; the "7.5ms" line is speculative and inaccurate (verified by us via runtime introspection).
- [ndeadly/switch2_controller_research](https://github.com/ndeadly/switch2_controller_research) — protocol docs + decrypted BLE captures.
