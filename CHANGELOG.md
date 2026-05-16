# Changelog

All notable changes to Joyglide are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
this project adheres to [Semantic Versioning](https://semver.org/).

## [0.1.1] — 2026-05-16

First post-launch release. Two real bug fixes surfaced during live
testing of the v0.1.0 build, plus opt-in observability tooling that
turned the project's latency claim from estimate into measurement.

### Fixed

- **Accessibility permission flow on rebuilds.** PyInstaller signs
  each build with a fresh hash; macOS TCC keys entries by
  `(bundle_id, code-signature)`, so stale entries from previous
  builds caused the system prompt to silently no-op. The app now
  runs `tccutil reset Accessibility com.opsthomaz.joyglide` before
  `request_accessibility()` on startup when permission is missing,
  guaranteeing a fresh prompt aligned with the running signature.
  A new "✓ I granted — Quit & Reopen" button in the warning modal
  performs Apple's canonical "quit and reopen" workflow (the running
  process's TCC cache does not invalidate live when the user
  grants — officially documented behavior).
- **Double-click detection no longer false-positives across positions.**
  Native macOS uses time + position (`NSEvent.mouseSlopForDoubleClick`);
  ours was time-only. Two single-clicks 400 ms apart at different
  positions (e.g. tab A then tab B in Firefox) were tagged with
  `click_count=2` and treated as a double-click on the wrong target.
  Added ±5 px position check; auto-double-click still fires correctly
  on rapid same-spot presses.

### Added

- **Latency instrumentation** (`latency_trace.py`, off by default
  via `settings["latency_trace"]`). Captures per-event
  `time.perf_counter_ns()` checkpoints at BLE callback entry and
  around `CGEventPost`; emits `p50/p95/max1s` per span every second
  via applog. Worst-frame samples record active profile in context
  for outlier diagnosis. 20 tests in `tests/test_latency_trace.py`.
  Zero hot-path cost when off (one bool check).
- **Empirical latency budget** documented in `docs/RESEARCH.md` §6
  (Tier S, instrumented 2026-05-16). Measured on Mac16,12 (MacBook
  Air M4), macOS 26.4.1, Python 3.13.13, JC2 firmware 2.1.4.1:
  Joyglide userspace pipeline (BLE callback → CGEventPost return)
  is **p50 ~110 µs / p95 ~163 µs** at steady state — under 1% of
  the 30 ms macOS BLE LL interval budget. Replaces the previous
  "microseconds, negligible" estimate with measurement.

### Changed

- README rewritten for v0.1.0 launch parity: CI / Python / license
  badges, Mermaid pipeline diagram, table of contents, a new "How
  we verify" section surfacing the project's 5 verification
  mechanisms (200+ tests · cosmic-ray mutation testing · tier-rated
  protocol claims · import-linter contracts · latency
  instrumentation), and a measured-latency line in the Performance
  section. Heritage cleanup: removed leftover "fork extends"
  language and `v0.3.0/v0.4.0` version refs that were pre-relaunch
  vestiges; bumped `python3.14` references to `python3.13` to
  match `requires-python`; recounted "~2500 lines" to the accurate
  "~6,000 lines of production Python (plus ~3,200 lines of tests)".
- `packaging/run_cosmic_ray.sh` for any non-`u16_delta` target
  now works correctly — the previous version produced an invalid
  TOML (multi-line `test-command` string with unescaped newlines)
  and silently errored out. `parser/battery.py` mutation run is
  now reproducible end-to-end via the script.

---

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
- **Adaptive BLE-rate drain** — pump auto-adjusts to whatever rate
  the OS negotiated (~33 Hz macOS, ~67 Hz Windows). Same code, no
  toggles.
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
