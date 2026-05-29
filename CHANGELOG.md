# Changelog

All notable changes to Joyglide are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

Audit-remediation pass: bug fixes, test-gap closure, value validation,
and dead-code removal surfaced by a full-codebase review. All behind the
project's standard gates (236 tests, ruff / pyright / mypy / import-linter
/ xenon green) and the cursor path was re-validated on hardware.

### Fixed

- **Windows multi-controller mouse race.** `osio/mouse/windows.py`
  reused a single module-level `MOUSEINPUT` struct across every
  `InputSimulator`; with two Joy-Cons (each on its own daemon-thread
  event loop) concurrent `_send_mouse` calls could clobber the shared
  struct mid-`SendInput`. Now serialised with a module lock so fill+post
  is atomic. The macOS / Linux backends were already per-instance.
- **Silent `add_player` failures.** `main.py`'s connect-future
  done-callback never retrieved the result, so an exception inside
  `add_player` vanished. It now logs `_f.exception()`.
- **Reconnect client leak.** `ble.connection.maintain_connection_loop`
  overwrote the dead `BleakClient` without disconnecting it, leaking the
  CoreBluetooth handle across reconnect cycles. Now best-effort,
  bounded `disconnect()` before recreating.

### Added

- **`parser/mouse_optical.py` test coverage** — the flagship cursor
  parser had none. New `tests/test_mouse_optical.py`: property tests +
  exact-value pins for the lift-off sentinel, deadzone, acceleration
  curve, sensitivity, gaming bypass, and wrap-around delta (CLAUDE.md §5).
- **Protocol-sequence tests** — `enable_mouse` two-write order + 0xFF
  payload, `set_leds` patterns, and `post_connect_setup`'s
  Cancel-before-LEDs ordering (the most-regressed paths, previously
  untested).
- **Settings value validation** (`user_preferences.validate_settings`)
  — clamps out-of-range / wrong-typed hand-edits to documented defaults,
  guarding the hot path. 13 tests.
- **Shared `utils.unpack_stick_12bit`** + named `STICK_*` offset
  constants, removing the duplicated 12-bit unpack between
  `parser/sticks.py` and `utils.decode_joystick`.

### Changed

- IMU timestamp Tier-S test now derives the 1 MHz rate from the hardware
  observation (30000 ticks / 0.030 s) instead of a self-referential
  `assert CONST == LITERAL` (CLAUDE.md §5).
- Strengthened two weakly-discriminative tests (BLE-period EMA; the
  power-info 1 Hz throttle now uses a controlled clock and verifies both
  block and release).
- Corrected stale docstrings in `parser/imu.py` and `parser/constants.py`
  that claimed the IMU feature bit is dropped when `imu_enabled` is off —
  the mask is always `0xFF`; the setting only gates parsing.

### Removed

- Dead code: the `JoyCon.settings` field, four unused IMU per-field
  offset constants, and write-only `_left_down` / `_right_down` /
  `_last_click_time` (+ an unused `import time`) in the Windows/Linux
  mouse backends.

### Verified (Tier S — hardware)

- **Cursor pipeline re-validated on a Joy-Con 2 (R) over BLE on macOS
  (2026-05-29).** Full manual pass — move + lift-off, click, stick
  scroll, ⌃⌥M pause/resume, profile switching — all correct. Measured
  `CGEventPost` latency at steady state **p50 ≈ 50–75 µs / p95 ≈
  85–160 µs** (cold-start n=1 outlier of 15.95 ms excluded). A Bluetooth
  toggle-off drove the reconnect loop through bounded exponential backoff
  (1.0 → 5.1 s) and it recovered via a fresh `BleakClient` with the
  cursor resuming — exercising the reconnect path including the
  client-cleanup fix above.

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
