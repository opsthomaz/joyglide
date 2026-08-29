# Joy-Con 2 — Technical Research and Final Architecture

Updated after the high-performance engine implementation (May 2026).

---

## 1. The Hardware and the Bottleneck (The 33 Hz Wall)

**The myth:** the Joy-Con 2 optical sensor is slow, or Mac Bluetooth is bad.
**The truth:** the physical sensor reads at high rate, but the Bluetooth radio is locked at the OS level.

**The reason:** macOS enforces a minimum *Connection Interval* of **30 ms (33 Hz)** for any generic BLE peripheral that doesn't formally declare itself as `HID-over-GATT` (Profile 0x1812, the standard for mice and keyboards, which allows 11.25 ms / 88 Hz). The Joy-Con 2 uses a custom Nintendo protocol and doesn't carry that signature.

**Our reverse-engineering attempts (frustrated by the hardware):**
1. Explored every secondary characteristic (UUID): none runs above 33 Hz.
2. Tried to force the latency via public APIs (`setDesiredConnectionLatency`): macOS swallowed the request and stayed at 33 Hz.
3. Injected code via PyObjC into Apple's private APIs (`IOBluetoothHostController / enableFastLeConnection`): macOS sent the request, but the Joy-Con firmware rejected the negotiation outside the Switch's native protocol, lit error LED 3 and dropped the connection. **Correction (2026-08-29):** strings in `bluetoothd` 26.6.2 show `enableFastLeConnection:` is the *Find My* LTK-caching SPI ("Fast LE Connection with no LTK is unsupported"), not a connection-interval control — that attempt targeted the wrong API.
4. **Private `connectPeripheral:options:` keys (2026-08-29, macOS 26.6.2, JC2-R fw 2.1.4.1 — Tier S).** `bluetoothd` contains undocumented keys `kCBConnectOptionOverrideMinCIFrames` / `MaxCIFrames` (interval in 1.25 ms frames), `kCBConnectOptionRequiresLowLatency`, `kCBConnectOptionLatencyCritical`, and the framework exports `CBConnectPeripheralOptionLatencyCritical`. Injected through bleak's CoreBluetooth backend (proxying `CBCentralManager.connectPeripheral:options:`; harness on branch `exp-ble-interval`, env `JOYGLIDE_BLE_OPTS`) and measured with the pump's packet-period EMA inside Joyglide, **one option per fresh connection**: `CIFrames=12` (15 ms) → 33.3 Hz; `CIFrames=6` (7.5 ms) → 33.3 Hz; `RequiresLowLatency` → 33.3 Hz; `LatencyCritical` → 33.3 Hz; public `CBConnectPeripheralOptionLatencyCritical` → 33.2 Hz; all four combined → 33.3 Hz (30.0 ms in every case). All accepted without error and ignored — consistent with the `com.apple.bluetooth.latencyCritical` entitlement string next to them. Also found but untested (needs `sudo` + `bluetoothd` restart): `DebugOverrideConnectionMin/MaxCIFrames` in `/Library/Preferences/com.apple.Bluetooth.plist`.

**Hardware conclusion:** the door is locked from both sides. It is impossible to receive raw data above 33 Hz on macOS from an ordinary (non-entitled) process. The Joy-Con 2 itself never requests an interval (ndeadly: the console sets 5 ms with a vendor HCI command), so the host default always wins: 30 ms on macOS, 15 ms on Windows 11 via `ThroughputOptimized`, anything on Linux/HCI. Cheap escapes that bypass Apple's stack: an ESP32-S3 running `TommyWabg/Switch2Connect`'s bridge firmware (7.5 ms, ~133 Hz, raw JC2 reports over USB CDC, framing `aa 55 len chan`) or the Joy-Con 2 Charging Grip over USB (HID PIDs 0x2066/0x2067, `bInterval` 4 ms = 250 Hz — unverified on macOS).

### 1.1. Experimental confirmation of the "Set Report Rate?" descriptor

After cloning [ndeadly's research](https://github.com/ndeadly/switch2_controller_research) (into `research/ndeadly_switch2/`), we found a descriptor with UUID `679d5510-...` labeled "Set Report Rate?" — a sibling of every input characteristic. The decrypted pcaps show the Switch console writing **`0x85 0x00`** to that descriptor before enabling notifications, and the Joy-Con replies at **200 Hz** on a 5 ms connection interval.

We reproduced the console's exact procedure on macOS through BLE exploration during development (5 phases × 6 seconds each):

| Phase | Setup | Measured rate |
| --- | --- | --- |
| A | Input 0x05, no rate write | 32.8 Hz |
| B | Input 0x05 + `0x85 0x00` to descriptor 0x000c | 33.2 Hz |
| C | Input 0x05 + various values (`0x05/0x01/0xFF`) | ~33 Hz |
| D | Input 0x07/0x08 (handle 0x000e), no rate | 32.5 Hz |
| **E** | **Input 0x07/0x08 + `0x85 0x00` to descriptor 0x0010 (exact replica of the console)** | **32.3 Hz** |

The controller **accepts** the write (no error, no disconnect). But macOS **pins the connection interval at 30 ms in the Link Layer** regardless of what the application does. Result: 33 Hz is a mathematical ceiling on macOS, experimentally confirmed.

### 1.2. Cross-OS comparison

| OS | App-side default | App-side with API/HCI tweak | Real measured rate |
| --- | --- | --- | --- |
| **macOS** (verified by us) | 30 ms / 33 Hz | **Blocked** — public/private APIs tested and ignored; descriptor `679d5510` accepted but no effect | **33 Hz** |
| **Windows 10/11** | 60 ms / 16 Hz (per ndeadly) | `BluetoothLEPreferredConnectionParameters.ThroughputOptimized` → **15 ms / ~67 Hz** ✅ | **~67 Hz** |
| **Linux (BlueZ)** | Configurable | 5 ms / 200 Hz via direct HCI | 200 Hz |

**Honesty correction about Windows:** earlier versions of this document promised "7.5 ms / 133 Hz" on Windows, citing comments in [TheFrano/joycon2cpp](https://github.com/TheFrano/joycon2cpp) and [Misaka10571/joycon2-connector](https://github.com/Misaka10571/joycon2-connector). Those comments are **speculative** — both projects use the same `ThroughputOptimized` preset we use, and the WinRT preset is hard-coded to `min == max == 12 units` (12 × 1.25 ms = **15 ms**, ~67 Hz). Verified by live-object introspection during the exploration session for this repo. There is no public constructor for `BluetoothLEPreferredConnectionParameters` accepting custom values, so 15 ms is the practical ceiling on Windows via that API.

The clones live in `research/thefrano_joycon2cpp/` and `research/misaka_joycon2_connector/` for inspection.

**Linux** remains the only fully "open" platform — BlueZ allows arbitrary HCI parametrization. The Linux backends ship in this repo (`osio/mouse/linux.py`, `osio/hotkey/linux.py`, `osio/boost.py` Linux branch) but are **experimental — present and integrated, but not yet hardware-verified** at v0.1.0; hardware confirmation reports are welcome. **Windows** comes second (~2× macOS, not ~4× as previously promised). **macOS** comes last by Apple policy, with no known workaround.

---

## 2. The Architectural Solution: The Delta-Time Pump

Since we can't receive fast packets over the air, we abandoned trying to modify the radio and solved the problem entirely in software math.

We created a separate asyncio task (the **Pump**) decoupled from the Bluetooth read thread.
- On every 33 Hz packet from the Joy-Con, we just push the distance into an "accumulator" (a bucket).
- The Pump wakes up physically synchronized with the user's monitor **refresh rate** (e.g., every 16.6 ms for a 60 Hz screen, or 8.3 ms for 120 Hz ProMotion).
- On wake-up, the Pump reads the high-precision CPU clock (`time.perf_counter`) and computes the **delta time (Δt)**.
- Based on Δt and the user's profile, it drains the mathematically perfect proportion of the bucket and injects it into the screen.

**Result:** we transform a "stuttering" 30 ms physical read into a continuous, perfect sub-pixel slide curve, perfectly matched to the monitor's VSync.

---

## 3. Library Bypass (Extreme Optimization)

The generic `pynput` library was completely removed from the project. It generated a lot of overhead on the main Python thread, rounding numbers and creating unnecessary objects.

**The solution (native sub-pixel):**
We descend straight into the macOS guts via the `Quartz.CoreGraphics` library.
- We avoid `int()` calls. All mouse injections now accept `float`, letting macOS render motion between screen sub-pixels.
- Screen-edge checks (`clamp`) became literal pre-computed `if`s to reduce Python function-call overhead in the *hot path*.
- The final event (`CGEventPost`) is injected natively into WindowServer, fooling the Mac into believing it's dealing with a real Apple trackpad.

---

## 4. Motion Profiles

We implemented three different drainage and acceleration logics to embrace any Air Mouse use case:

1. **🚀 Dynamic (default / productivity):**
   - Smart acceleration (`1.0×` to `2.5×` in the mid range, based on `speed²`).
   - Exponential scroll (cubic curve) — pushing the analog stick all the way down scrolls pages quickly.
   - Pump with *delta time* to smooth the 33 Hz BLE rate to display refresh without perceptible latency.
   - "Fast" idle brake (keeps 30 %/tick after 60 ms idle) — clean stop, no drift.
2. **🎯 Gaming / FPS (competitive):**
   - Acceleration disabled (`1:1` linear).
   - Optical-sensor deadzone reduced to **zero**.
   - **Pump bypass:** motion is emitted **directly on BLE packet arrival** via `CGEventPost`, without going through the accumulator. Saves up to 16.67 ms of pump-tick wait (60 Hz). It's the lowest input lag the architecture can provide — below this only with BLE bypass (not possible on macOS without HID-over-GATT).
3. **🍿 Cinematic (couch / inertia):**
   - Base multiplier reduced (`0.8×`).
   - Slow drainage (`Drain = 0.25`, keeps 75 % of the accumulator per tick) during motion.
   - **Custom idle brake (keeps 65 %/tick = 35 % decay)** — after the user stops moving, the cursor keeps gliding for ~150–250 ms before zeroing. Real inertia, masking hand tremors and producing smooth "in-air" movement.

**Note on `sensitivity`:** the global sensitivity multiplier is applied in **every** profile (Gaming included). Gaming's "1:1" claim is exact only when `sensitivity = 1.0`. This is intentional — almost nobody wants to lose the sensitivity slider just by entering competitive mode.

---

## 5. Next Steps (front-end)

The application base and the input engine are complete and operating at native driver level. The focus now should be migrating the settings scattered across the system tray (`pystray`) and primitive windows (`tkinter`) into a unified, modern control panel (front-end).

---

## 6. Latency Budget — Empirical Measurement (Tier S, instrumented 2026-05-16)

Earlier sections estimate the userspace latency contribution at "microseconds, negligible vs the 30 ms BLE cap." This section replaces that estimate with **instrumented measurement** via the `latency_trace.py` module added on 2026-05-16 (settings flag `latency_trace`, off by default — zero cost when disabled).

The headline result: **the Joyglide userspace pipeline contributes ~110 µs at p50 from BLE callback dispatch to Quartz event injection, representing ~0.37% of the macOS BLE LL interval budget.** The bottleneck is provably and overwhelmingly the BLE LL interval, not the Python/CG code.

### 6.1 Methodology

**Hardware / software**

| Component | Version |
|---|---|
| Mac model | `Mac16,12` (MacBook Air M4) |
| macOS | 26.4.1 build 25E253 |
| Display | 1920×1248 @ 60.0 Hz (internal) |
| Python | 3.13.13 (Homebrew + python-tk@3.13) |
| `bleak` | 3.0.2 |
| Joyglide | post-v0.1.0 main (commit at measurement time) |
| Joy-Con 2 | JC-R, firmware **2.1.4.1** (verified via cmd `0x10/0x01`) |

**Three perf_counter_ns timestamps per event**

```
t0  =  bleak notification callback entry        (set by solo_logic, contextvar)
t1  =  immediately before CGEventPost           (set by osio.mouse.macos._post)
t2  =  immediately after CGEventPost            (set by osio.mouse.macos._post)
```

**Two derived spans**

| Span | Definition | What it isolates |
|---|---|---|
| `internal_us` | `t1 − t0` | Joyglide's own pipeline: BLE callback → parser → dispatch → CGEvent build |
| `cgevent_us` | `t2 − t1` | Quartz's `CGEventPost` cost only (kernel-side HID injection) |

`internal_us` is recorded only when `t0` is visible to `_post` — i.e. the synchronous call path. That covers (a) all motion in **Gaming profile** (parser calls `mouse_move` directly within the BLE callback) and (b) **all button events** in any profile (parser.buttons calls `mouse_down`/`mouse_up` synchronously). Motion in Dynamic / Cinematic flows through the async `engine.motion_pump` task and the contextvar is not visible there — so for those profiles `internal_us` reflects button events only and is intentionally not recorded for motion (we don't have an honest single `t0` to subtract from when the pump fires asynchronously from packet N+2 carrying motion from packets N and N+1).

`cgevent_us` is recorded on every `_post` call regardless of profile and is therefore directly comparable across profiles.

**Sampling**

- Per-span ring buffer: `deque(maxlen=200)` ≈ **6 s at 33 Hz** (1 BLE packet per 30 ms macOS cap).
- Aggregation: emit `p50 / p95 / max1s / alltime_max` once per second per span.
- The `alltime_max` carries the active **profile** in its captured context — outliers can be diagnosed (Gaming vs Dynamic provenance).
- Workload: continuous JC2 motion on a hard surface + occasional button presses, ~30 s per profile, with steady-state samples (last 30 emissions) used for the headline numbers.

### 6.2 Results — Gaming profile (synchronous path)

Last 30 steady-state emissions during continuous motion (`profile=gaming`):

| Span | p50 | p95 (median across emissions) | max1s (worst per-second window, median) | alltime_max (single all-session worst) |
|---|---|---|---|---|
| `internal_us` | **36 µs** | 51 µs | 92 µs | 67,973 µs (one-time startup outlier) |
| `cgevent_us` | **74 µs** | 112 µs | 580 µs | 11,338 µs (one-time startup outlier) |
| **Total userspace (sum)** | **~110 µs** | ~163 µs | ~672 µs | — |

Range across the 30 sample windows: `internal_us` p50 26–39 µs · `cgevent_us` p50 49–82 µs. Tight steady-state distribution.

### 6.3 Results — Dynamic profile (async pump path)

For this profile `cgevent_us` is the directly comparable metric (motion + buttons both call `_post`, profile-independent kernel cost). `internal_us` here reflects **button events only** and has small sample counts.

| Span | p50 | p95 | Notes |
|---|---|---|---|
| `cgevent_us` | **~74 µs** | ~112 µs | Identical to Gaming within noise (profile-independent — Quartz cost) |
| `internal_us` (buttons only) | ~30–50 µs | — | Small-N; consistent with Gaming's measured internal_us, suggesting the parser+dispatch cost is the same whether the BLE callback calls `mouse_move` (Gaming) or `mouse_down` (button) |

**Pump-quantization overhead** (not measured here, structural): the pump runs at display refresh (60 Hz on this hardware = 16.7 ms tick), so a packet whose motion is accumulated at `_dx_accum` waits 0–16.7 ms before the next pump tick drains it. That latency is **a deliberate trade for smoothness** (interpolation across BLE packets) and is independent of the userspace pipeline cost measured above.

### 6.4 Outliers

| Span | alltime_max | Interpretation |
|---|---|---|
| `internal_us` | 67,973 µs (≈ 68 ms) | One occurrence, very early in the session (first BLE packet at n=1). Consistent with interpreter warmup, async-loop first-iteration cost, or initial GC. Not repeated in steady state. |
| `cgevent_us` | 11,338 µs (≈ 11 ms) | Same pattern — single early occurrence. Likely WindowServer or HID event-tap initialization the first time we POST through the path. |

Steady-state `max1s` (the per-second worst-case during normal use) never exceeded **704 µs** for `cgevent_us` or **185 µs** for `internal_us` across 5+ minutes of recording. p95 across the whole session remained ≤ 128 µs. The single-event maxes are interpreter/OS startup artifacts, not pathology.

### 6.5 Conclusion

The Joyglide userspace pipeline contributes **~0.37 % of the end-to-end latency budget at p50** (110 µs / 30,000 µs) and **~0.54 % at p95** (163 µs / 30,000 µs) under macOS BLE LL constraints. The 30 ms Apple QA1931 cap accounts for >99 % of the budget; the Python parser + dispatch and the Quartz event-post together account for less than 1 %.

This validates the architectural claim from §1: **the bottleneck is the BLE link-layer interval imposed by macOS for non-HID-over-GATT peripherals, not the Python / CGEvent pipeline.** Further optimization of the userspace code path would shave microseconds invisible to a user; meaningful end-to-end latency gains require addressing the BLE transport (Linux + BlueZ at 5 ms, external USB BT dongle bypassing the Apple stack, or HID-over-GATT firmware on the JC2 — none of which are software-only on macOS).

### 6.6 Reproducing

```bash
# Enable trace
python3 -c "
import json, pathlib
p = pathlib.Path.home() / 'Library/Application Support/joyglide/settings.json'
d = json.loads(p.read_text())
d['latency_trace'] = True
p.write_text(json.dumps(d, indent=2))
"

# Run Joyglide directly (binary mode → stderr to terminal),
# OR via .app (search Console.app for senderImagePath = Joyglide):
~/joyglide/dist/Joyglide.app/Contents/MacOS/Joyglide 2>&1 | grep '⏱  latency'
```

Move the connected JC2 on a hard surface for 60 s+. Switch `profile` between `dynamic` and `gaming` via the UI's Performance tab to compare paths. Per-second log lines:

```
⏱  latency.cgevent_us  n=200 p50=74us p95=112us max1s=580us alltime_max=11338us
⏱  latency.internal_us n=200 p50=36us p95=51us  max1s=92us  alltime_max=67973us profile=gaming
```

The `latency_trace` module is `~200` LOC at the project root (`latency_trace.py`); tests at `tests/test_latency_trace.py` (20 tests, ring-buffer aggregation + emission throttle + worst-frame context capture). The instrumentation is permanent observability infrastructure, not a debug branch — re-runnable any time by flipping the settings flag.
