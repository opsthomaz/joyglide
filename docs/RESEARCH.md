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
3. Injected code via PyObjC into Apple's private APIs (`IOBluetoothHostController / enableFastLeConnection`): macOS sent the request, but the Joy-Con firmware rejected the negotiation outside the Switch's native protocol, lit error LED 3 and dropped the connection.

**Hardware conclusion:** the door is locked from both sides. It is impossible to receive raw data above 33 Hz on macOS.

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
