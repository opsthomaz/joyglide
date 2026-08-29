# Changelog

All notable changes to Joyglide are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

Full-codebase audit (2026-08-29): line-by-line review, re-verification of
every upstream protocol source, and a macOS 26 / toolchain refresh. All
gates green (268 tests, ruff / pyright / mypy / import-linter / xenon).

### Fixed

- **App kept the Mac from idle-sleeping.** `osio/boost.py` passed the
  literal `0x00FFFFFF` to `beginActivityWithOptions_reason_` believing
  it was `NSActivityUserInitiatedAllowingIdleSystemSleep`; that value is
  `NSActivityUserInitiated`, which carries `IdleSystemSleepDisabled`.
  Now uses the named Foundation constants (AllowingIdleSystemSleep =
  `0x00EFFFFF`) plus `NSActivityLatencyCritical` for the pump's timers.
  Tier B — constant values verified against pyobjc 12.2 on macOS 26.6.
- **No motion for delta-reading consumers.** `CGEventCreateMouseEvent`
  leaves `kCGMouseEventDeltaX/Y` at 0, so FPS games with a captured
  cursor and `NSEvent.deltaX` users saw nothing from the Gaming profile.
  Moved / Dragged events now carry the rounded per-event delta.
- **⌃⌥M could die silently.** The hotkey `CGEventTap` never handled
  `kCGEventTapDisabledByTimeout` / `ByUserInput`; a GIL stall during a
  BLE burst was enough for WindowServer to disable it for good. The
  callback now re-enables the tap.
- **Stale `.app` version.** `Info.plist` carried a hand-maintained
  `0.1.0`; the spec now reads the version from `pyproject.toml`.
- **Corrupt `settings.json` crashed at import** with no window. It is
  now moved to `settings.json.corrupt` and defaults are recreated.
- **Auto-hidden Dock flickered up/down** when pushing the cursor
  against the bottom edge. macOS bounds a real pointer to
  `size − 1/64` per axis (`CGWarpMouseCursorPosition` → y = 1111.984
  on a 1112-pt display) but accepts posted events anywhere; our clamp
  parked the cursor at exactly `size`, 1 pt outside the valid range,
  where the Dock's edge trigger fired but its hit-test failed. Clamp
  now uses `size − 1/64`. Tier S (measured; Dock verified fixed).
- **Pump kept posting 60 zero-pixel events/s for ~10 s after every
  stop** (event-tap capture attributed by source PID): the idle brake
  shrinks the accumulator geometrically but never reached 0.0.
  `engine.tuning.settle_accumulator` snaps |accum| < 0.05 px to zero.
- **Bogus multi-second `latency.internal_us` sample at pump start**
  (6.15 s observed): `create_task` copied the BLE callback's
  contextvars (latency_trace's t0) into the pump task. `start_pump`
  now uses a fresh `contextvars.Context()`.
- Docstrings in `main.py` / `ARCHITECTURE.md` still described one event
  loop per controller; every controller shares the `bg_loop` singleton.

### Added

- **`swap_click_buttons` setting** (Settings tab → Input): trigger
  ZL/ZR = left click, shoulder L/R = right click. Default off keeps the
  documented layout. The release event always pairs with the press that
  was actually fired, so toggling mid-press can't strand a button.

### Changed

- **Display refresh rate via `NSScreen.maximumFramesPerSecond`**
  (macOS 12+), replacing the hard-coded ProMotion model list that
  stopped at `Mac16,x` and would have run the pump at 60 Hz on newer
  120 Hz MacBook Pros. Tier B.
- **BLE/pump thread runs at `QOS_CLASS_USER_INTERACTIVE`**
  (`pthread_set_qos_class_self_np`) so Apple Silicon keeps it on a
  performance core. `os.nice(-10)` never affected core placement and
  always fails without root.
- `ble.connection.connect_and_setup` passes the `BLEDevice` (not its
  address) so bleak's CoreBluetooth backend reuses the discovered
  peripheral instead of re-scanning; `scan_device` surfaces bleak ≥2's
  `BleakBluetoothNotAvailableError` as a user-visible status.
- Removed `gc.collect()` from `Player.__init__` / `disconnect` — a full
  GC pass there stalls every other controller's pump for milliseconds.
- `LSMinimumSystemVersion` 10.15 → 11.0 (arm64-only binary; no Apple
  Silicon Mac predates Big Sur).
- Dependencies: bleak ≥3.0.2, pyobjc ≥12.2.1, PyInstaller ≥6.22.0
  (first release whose tkinter hooks handle Tcl/Tk 9), pytest ≥9;
  dropped the unused `py2app` pin. CI pinned to `macos-26`,
  `actions/checkout` v7, `actions/setup-python` v7.

### Verified (Tier S — hardware, 2026-08-29, JC2-R fw 2.1.4.1, macOS 26.6.2)

- Posted Moved/Dragged events carry `kCGMouseEventDeltaX/Y` end to
  end (listen-only event tap: 26–52 of 60 events/s with non-zero delta
  while moving; a probe event with delta 7/−5 arrived intact).
- `NSActivity` fix: with the old literal `0x00FFFFFF` the process holds
  `PreventUserIdleSystemSleep` (`pmset -g assertions`); with the named
  constants it holds no assertion.
- Reconnect after a Bluetooth off/on toggle succeeded on the first
  retry with the fresh `BleakClient`.
- `latency.internal_us` alltime_max after the contextvar fix: 115 µs
  (previously a 6.15 s outlier); `cgevent_us` p50 ≈ 90 µs, unchanged
  from the May baseline.
- Dock edge behaviour: auto-hidden Dock now reveals and stays.
- `swap_click_buttons` toggled live in Settings: R/ZR roles swapped and
  restored as expected.

### Research (2026-08-29 source re-verification)

- No upstream source contradicts any Tier-S constant (accel 4096/G,
  gyro 48000/360°, temp 25 + raw/127, current raw/100 mA, IMU
  timestamp 1 MHz). No Joy-Con 2 firmware change since 2.1.4.1 is
  documented anywhere public.
- `TropicalCyclone/switch2-controller-driver` (battery-current
  citation) was deleted; the identical code lives in
  `Nadeflore/switch2-controllers` (`controller.py:138`). Citations
  updated. `darthcloud/BlueRetro` archived 2025-12-14.
- New macOS-native reference `OZORDI/JoyCon2Mac` independently uses
  mask `0xFF` and subscribes input 0x05 + response `c765a961` together.
  `TheFrano/joycon2cpp` v1.3+ uses mask `0x37` inside a console-captured
  init (Tier C, Windows) — CLAUDE.md §3 wording softened accordingly.
- ndeadly (2026-04) renamed `0x03/0x02` to "Bluetooth Cancel" and notes
  the `0x03` family is likely meant for USB/rail; it still works over
  BLE here (Tier S).
- macOS 26 / 27b7: no CoreBluetooth, CGEvent or TCC changes affect this
  pipeline; macOS still does not pair Joy-Con 2 natively. Accessory
  Design Guidelines R30 (2026-06) keeps the 15 ms non-HID interval
  floor — the 33 Hz ceiling stands.

## [0.1.2] — 2026-05-30

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
- **Button-click mapping** — shoulder `L`/`R` = left click, trigger
  `ZL`/`ZR` = right click; rapid press within 400ms triggers
  double-click. (Earlier wording of this entry had the pairs crossed.)
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

[0.1.2]: https://github.com/opsthomaz/joyglide/releases/tag/v0.1.2
[0.1.0]: https://github.com/opsthomaz/joyglide/releases/tag/v0.1.0
