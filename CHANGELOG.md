# Changelog

All notable changes to Joyglide are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
this project adheres to [Semantic Versioning](https://semver.org/).

## [0.1.0] — 2026-05-15

Initial public release.

Joyglide pairs a Nintendo Switch 2 Joy-Con over Bluetooth Low Energy
and turns its optical sensor into a native desktop mouse.

### Features

- **Cross-platform** — runs on macOS and Windows with native input
  APIs in each backend (Quartz CoreGraphics / Win32 `SendInput`); no
  Electron, no Qt.
- **Three motion profiles** — Dynamic (speed-² acceleration with
  pump-interpolated cursor at display refresh), Gaming/FPS (raw 1:1,
  zero deadzone, pump bypassed for minimum input lag), and Cinematic
  (slow drain with an extended idle inertia tail).
- **Sub-pixel cursor motion** — native float precision on macOS,
  software-accumulated on Windows.
- **Multi-controller support** — pair multiple Joy-Cons at once, each
  with its own dashboard row; switch a controller between left and
  right at runtime without disconnecting.
- **Button-click mapping** — `L`/`ZL` = left click, `R`/`ZR` = right
  click; rapid press within 400ms triggers double-click.
- **Analog-stick scroll** — cubic curve in Dynamic/Cinematic, linear
  in Gaming.
- **Live battery readout** parsed from input report `0x05`.
- **Global pause hotkey** — `Ctrl+Alt+M` (Windows) / `⌃⌥M` (macOS)
  freezes input without disconnecting.
- **Vibration on connect** plus a tray debug control.

### Protocol research

Ships hardware-verified corrections to the open Joy-Con 2 BLE protocol
catalog, validated against real hardware: IMU calibration constants
(including the 1 MHz timestamp scale), the battery-current scale
(raw / 100 = mA), the `0xFF` feature-mask requirement, and the
write-command length-byte semantics.

### Heritage

Joyglide descends from prior reverse-engineering work — most directly
[moutella/joycon2mouse](https://github.com/moutella/joycon2mouse). See
`NOTICE` for full attribution.

[0.1.0]: https://github.com/opsthomaz/joyglide/releases/tag/v0.1.0
