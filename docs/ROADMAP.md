# Roadmap

A non-binding view of where Joyglide could go next. Items are
unordered within each section — pick whatever interests you. PRs
welcome on any of these (see [`.github/CONTRIBUTING.md`](../.github/CONTRIBUTING.md)).

## 🎯 Tier 1 — high impact, well-scoped

### Linux: hardware verification
**Difficulty:** Medium • **Files to touch:** mostly 0 — verification, not new code

The Linux backends already ship in v0.1.0:

- `osio/mouse/linux.py` — uinput cursor injection
- `osio/hotkey/linux.py` — global hotkey via evdev
- `osio/boost.py` — Linux branch using `hcitool lecup` for connection
  parameter override
- `requirements.txt` already declares `evdev>=1.6.1; sys_platform == 'linux'`

What's missing is **hardware confirmation on a real Linux desktop with
a real Joy-Con 2**. The code is integrated but unverified, so Linux is
documented as experimental at v0.1.0. The contribution opportunity is:
pair a JC2 on Linux + BlueZ, run Joyglide, and report whether scan /
connect / cursor injection / hotkey / boost actually work end-to-end —
plus fixes for whatever breaks.

**Why it matters:** Linux + BlueZ in principle unlocks **200 Hz native**
— the same rate the Switch console itself uses. macOS is capped at 33 Hz
by Apple policy; Windows tops out at ~67 Hz via WinRT. Linux is the only
host OS where we can saturate the Joy-Con's native rate. Optional:
a `docs/LINUX.md` build/run guide once the path is validated.

### Pro Controller 2 / NSO GameCube support
**Difficulty:** Easy-Medium • **Files to touch:** 2-4

- Detect controller variant via manufacturer-data byte 5 (`0x69` =
  Pro Controller 2; coffincolors/jc2mouse already does this).
- Subscribe to input report 0x09 (Pro) or 0x0A (GameCube) instead of
  0x05 — both have different button bitmask layouts.
- Extend `parser/buttons.py` with new mask tables.
- Optional: per-variant LED patterns / vibration presets.

**Why it matters:** Same protocol, different controllers — all docs
are in [`ARCHITECTURE.md`](ARCHITECTURE.md). Big user-base expansion.

### macOS universal2 build
**Difficulty:** Easy • **Files to touch:** 1-2 (CI workflow + spec)

Configure CI to install dependency wheels with
`--platform macosx_*_universal2` and add `target_arch='universal2'`
in the PyInstaller spec. Eliminates Rosetta 2 friction on Intel Macs.

### Windows ARM64 build
**Difficulty:** Easy-Medium

Build PyInstaller on a `windows-11-arm` GitHub runner. Spec stays the
same. Surface Pro X / Snapdragon X laptops would run native instead
of via x64 emulation.

## 🎯 Tier 2 — interesting but bigger

### IMU-based motion prediction
**Difficulty:** Medium-Hard

**Status at v0.1.0:** `parser/imu.py` exists and decodes the
18-byte Motion Data block at offset 0x2A of input report 0x05
(timestamp + temperature + accel + gyro, calibration scales
verified on hardware). `engine/predictor.py` exists with optical-
velocity prediction (default off, opt-in via
`motion_prediction_enabled`).

**Honest scope correction:** input report 0x05 carries ONE IMU
sample per packet, same rate as the optical sensor. So IMU on this
report cannot reduce inter-packet latency. The ~6-samples-per-packet
framing from earlier exploration notes referenced input reports
0x07/0x08 which carry 40-byte multi-sample motion blocks — but in
"unknown packed format" per ndeadly (see
`research/ndeadly_switch2/hid_reports.md`).

**Real-win path** (out of current scope):
1. Reverse-engineer the multi-sample packed format on
   reports 0x07/0x08 (no upstream documentation; needs hardware
   experimentation).
2. Switch primary subscription from 0x05 to side-specific
   — major refactor that loses our verified IMU calibration at 0x05's
   offset 0x2A, the magnetometer at 0x19, and the absolute-mouse path.
3. Implement gyro-aided cursor prediction using the multi-sample
   stream.

Easier wins available today: air-mouse mode (uses the IMU we
already parse, no multi-sample needed), per-profile sensitivity
tuning, etc.

### Air-mouse mode
**Difficulty:** Medium

Use the gyro/IMU as the cursor source (Wii Remote-style) instead of
the optical sensor. Useful for media center / presentation use. The
IMU data is already parsed (`parser/imu.py`); what's needed is an
`engine/` consumer that pipes gyro samples into the motion
accumulator (with an opt-in user setting and integration drift
compensation).

### Sub-pixel cursor on Windows
**Difficulty:** Hard

Win32 `SendInput` is integer-only. A virtual HID driver
(Interception or DriverKit) could expose true sub-pixel motion. Big
project — separate component.

## 🎯 Tier 3 — quality-of-life

### Per-axis sensitivity
**Difficulty:** Easy

Split the `sensitivity` setting into `sensitivity_x` / `sensitivity_y`
in `user_preferences.py` and the UI Performance tab.

### Localization (i18n)
**Difficulty:** Easy

Mirror Misaka10571/joycon2-connector's Chinese + English with `gettext`
or a simple JSON dict.

### Better installer
**Difficulty:** Easy

Native installers (.pkg / .msi) instead of .zip / .exe. Includes the
permissions setup (Accessibility prompt) and an Applications folder
symlink on macOS.

### Code signing + notarization
**Difficulty:** Medium • **Cost:** $99/yr Apple Developer + ~$300/yr
Microsoft cert

Eliminates the "Apple cannot check this app" warning on macOS and
the SmartScreen warning on Windows. Pays for itself in user trust
once the project has external users.

## 🎯 Tier 4 — nice-to-have / future-tier

### Multi-controller scroll lock
Today scroll comes from whichever controller has stick deflection. If
two are connected and both move sticks, the events interleave. A
"primary scroll source" setting would be deterministic.

### Vibration scripting
Currently we only play documented presets. Output Report 0x01 accepts
arbitrary 16-byte HD Rumble Data per ndeadly. Could expose a small
DSL for custom haptic patterns (notifications, button feedback).

### Battery-current as a proxy for "discharge rate" UI
We now parse Battery Current from offset 0x22. Could plot a small
gauge in the dashboard ("⚡ +400 mA charging" / "🔋 -180 mA").

### Dark / light theme follow OS
CustomTkinter currently hardcodes dark. Switching to "system" mode
would respect macOS / Windows preference.

## ❌ Not planned

These have been considered and rejected:

- **Cloud sync of settings** — no clear use case for a per-machine
  input config; risks introducing remote attack surface for zero
  user benefit.
- **Telemetry** — privacy-hostile and unnecessary for a hobby app.
  Issues are filed manually.
- **Custom protocol over WebSocket** to a paired phone — too far from
  the project's "JC2 → mouse" purpose.

---

**Want to pick something?** Open an issue or comment on an existing
one to coordinate. Most items here are good first contributions if
the difficulty is "Easy" or "Medium" and the relevant package is
already documented in [`ARCHITECTURE.md`](ARCHITECTURE.md).
