# Troubleshooting

Common issues with documented fixes. If you hit something not listed
here, please open a [bug report](https://github.com/opsthomaz/joyglide/issues/new?template=bug_report.md)
so we can add it.

## macOS

### "Apple cannot check this app for malicious software"

Expected on first launch — we ship binaries unsigned (no Apple
Developer Program subscription). To bypass:

1. Right-click the `.app` → **Open** → confirm.
2. After approving once, double-click works normally.

The roadmap item "Code signing + notarization" would eliminate this
warning permanently. Costs $99/yr.

### Cursor doesn't move even though the controller is connected

→ Likely a missing **Accessibility** permission. macOS requires apps
that synthesise input events to be in
**System Settings → Privacy & Security → Accessibility**, with the
toggle ON.

The app prompts for this on first launch. If you missed the prompt:

1. Open **System Settings → Privacy & Security → Accessibility**.
2. If `Joyglide` is in the list, toggle it ON.
3. If it's NOT in the list, the app's TCC entry got lost — re-launch
   from a Terminal: `open dist/Joyglide.app` (the system prompt
   will fire again).

### Cursor still doesn't move, but the app shows "Accessibility granted"

→ Stale TCC entry from an earlier build. macOS caches the trust
decision against `(bundle_id, code_signature)`. Each PyInstaller
build has a different ad-hoc signature, so an upgrade can produce a
"trusted but stale" entry.

The dashboard's accessibility-warning modal has a **Reset Permission
and Relaunch** button that runs:

```
tccutil reset Accessibility com.opsthomaz.joyglide
```

Click it. The app re-launches and prompts fresh.

### The .app icon is missing in the Dock when launched

Expected — `LSUIElement = True` in `Info.plist` makes this a
menu-bar-only app (the icon lives in the macOS menu bar, top-right).
Click that icon to access the dashboard.

### "Permission denied" or `EACCES` errors during scan

→ Bluetooth permission. macOS asks the first time the app scans;
make sure you accepted. If not, toggle:
**System Settings → Privacy & Security → Bluetooth → Joyglide**.

## Windows

### `convert_to returned null` at startup

→ `winsdk` is shadowing `winrt`. The two WinRT projections expose
overlapping types but they don't interoperate; mixing them in the
same process produces this exact error.

```powershell
pip uninstall winsdk
```

The project uses `winrt` only (via bleak). Don't install `winsdk`
separately.

### Cursor jitter / packets dropping

→ BLE driver age. Realtek/CSR drivers older than 2022 sometimes
ignore the `BluetoothLEPreferredConnectionParameters.ThroughputOptimized`
preset, falling back to 60 ms / 16 Hz. Upgrade via Windows Update or
the chip vendor's site.

A healthy `ThroughputOptimized` connection runs at `~67 Hz`. The app
does not expose a packet-rate counter, so use cursor feel as the
diagnostic: smooth low-lag motion = boost active; noticeably choppy
or sluggish cursor at ~16 Hz = driver not honoring the preset.

### "Enhance pointer precision" interferes with our acceleration

→ Disable Windows' built-in mouse ballistics so they don't compound
with our profile-based curves:

**Settings → Bluetooth & devices → Mouse → Additional mouse settings
→ Pointer Options → uncheck "Enhance pointer precision"**.

This is system-wide. If you prefer to keep it on for normal mouse
use, switch to the **Gaming** profile in Joyglide — it sets
multiplier = 1.0, mostly cancelling the layering issue.

### Anti-cheat games flag mouse input

→ Many anti-cheat systems (Vanguard, Easy Anti-Cheat, BattlEye) flag
synthetic input from `SendInput`. Same applies to any auto-clicker
or macro tool. Not relevant for desktop / non-anti-cheat use.

## Both platforms

### Joy-Con won't connect — scan times out

Several possible causes:

1. **Joy-Con is paired to a Switch already**. Hold the Sync button
   for ~3 seconds (between SL/SR on the rail) until the LEDs march
   back and forth.
2. **Bluetooth is off / paired with another device**. Check
   System Settings (macOS) or Settings → Bluetooth (Windows).
3. **Old firmware**. Update by re-pairing to a Switch 2 first; the
   console will push an OTA update.
4. **BT 4.0+ adapter required**. Pre-2014 hardware likely lacks BLE.

### After a disconnect, pressing a normal button doesn't reconnect

Expected — this is JC2 firmware behaviour, not an app bug.

For the host (Mac/PC) to find the controller, the controller has to be
**advertising** — broadcasting "hi, I'm here" packets on BLE. The JC2
only enters advertising mode when you **hold the sync button** (between
SL/SR on the rail). A normal button press (A/B/L/R/etc.) is meaningful
only while connected; while disconnected, the controller is in standby
at the radio level and those presses go nowhere.

Same constraint applies on the Switch 2 console: a Joy-Con that lost
its link needs either re-attaching to the rail (auto-bonds via contacts)
or holding sync. There's no software-only "wake on button press" path
on JC2 BLE.

### After a forced disconnect (BT cycled, sleep, range fade), reconnect succeeds but LEDs keep cycling

Was a real bug — fixed in [Unreleased]. The JC2 firmware has a quirk
documented by ndeadly: cancelling BLE advertising via subcommand
`0x03/0x02` should also stop the player-LED cycle, but the firmware
leaves the LEDs cycling indefinitely ("maybe a firmware bug?" per
ndeadly). On reconnect via the sync button, our `post_connect_setup`
now sends `Bluetooth Cancel` *before* `set_leds`, transitioning the
controller out of "advertising" state so the player-LED pattern
actually takes effect.

### Vibration on connect feels weak

Expected. Preset 0x03 (the "Connection" sample documented by ndeadly)
is intentionally subtle — a soft click-click. If you want a real
buzz, use **DEBUG → Test Vibration Preset** in the tray menu and
play with 0x01 (longest) or 0x05 (stronger click).

### Battery percentage seems wrong

The percentage is a linear approximation: 3300 mV → 0%, 4200 mV →
100%. LiPo discharge isn't actually linear — they tend to plateau
at ~3.7 V then drop steeply at the end. So a "70%" reading might
behave more like "85%" early in the discharge curve.

Battery current (since v0.2.12) is more reliable for a "real-time
charge/discharge" indicator. The dashboard could expose this in a
future release — see [`ROADMAP.md`](ROADMAP.md) "Battery-current as a
proxy for discharge rate UI".

### App freezes for several seconds when I open the tray menu

→ Should be fixed in v0.2.11 and later. If you're seeing this on a
recent build, please [open an issue](https://github.com/opsthomaz/joyglide/issues/new?template=bug_report.md)
with your OS version and which menu item triggered it.

The pre-v0.2.11 hang was caused by tray callbacks doing synchronous
work (Tk calls + JSON disk I/O) on the AppKit/Tk main thread; v0.2.11
moved both off the main thread.

### Multiple Joy-Cons interfering with each other

Today both connected controllers can move the cursor and scroll.
There's no "primary" / "secondary" distinction. If two users want to
share input, this works; if you want one Joy-Con as the "real" mouse
and the other for something else, file a feature request.

## Development / build issues

### `pytest` fails with `ModuleNotFoundError: No module named 'utils'`

→ You're not running from the project root, OR the `tests/conftest.py`
isn't being loaded. Run from repo root: `pytest -q`.

### CI fails with "import-linter contract violated"

→ Your PR added an import that crosses a boundary the architecture
forbids. Common cases:

- `parser/<x>.py` importing from `ui/` or `ble/` — parsers should be
  pure data decoders, not aware of either.
- `engine/<x>.py` importing from `parser/` — engine drives the pump,
  parsers feed the accumulator; they don't depend on each other.
- `osio/<x>.py` importing from any of the above — `osio` is a leaf.

Fix: refactor the offending import. If you genuinely need to
cross a boundary, propose a contract change in `.importlinter` with
justification in the PR description.

### macOS build: `zlib.__file__` AttributeError

→ This was a py2app-specific bug on Python 3.12+. We standardised on
PyInstaller for both OSes (see `packaging/joyglide_macos.spec`).
If you're getting this, you're using py2app — switch to:

```
./packaging/build.sh
```

### PyInstaller `.app` doesn't include some asset

→ Add it to `datas` or `hiddenimports` in
`packaging/joyglide_macos.spec` (or `..._windows.spec`). The spec
already lists every package's leaf modules explicitly to defend
against PyInstaller's static analysis missing dynamic imports.
