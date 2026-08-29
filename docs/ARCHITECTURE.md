# Joyglide — Architecture & Research Notes

## What This Is

A macOS app that connects a Nintendo Switch 2 Joy-Con via BLE and uses it as a mouse. The Joy-Con has an optical sensor on the bottom (like a physical mouse) that this app reads to move the cursor. Built in Python using bleak (BLE), Quartz CoreGraphics (native event injection), and CustomTkinter (UI).

---

## Protocol claim tier hierarchy

The Joy-Con 2 BLE protocol is community-reverse-engineered. Every claim
in this document about offsets, command IDs, response formats, or
calibration constants is implicitly or explicitly graded against the
project's six-level tier list (referenced from `CLAUDE.md` §1 and used
by the `.claude/agents/` verification subagents):

- **Tier S** — empirical convergence: multiple independent sources
  agree AND the claim has been hardware-tested on a real JC2 in this
  repo. Trumps everything else.
- **Tier A** — methodical reverse engineering with traceable
  methodology (e.g. ndeadly's decrypted pcaps, german77's Wireshark
  dissector).
- **Tier B** — official platform documentation (Apple QA1931, Microsoft
  Learn, switchbrew.org).
- **Tier C** — working third-party driver code (trust the code, not
  the comments).
- **Tier D** — community sources (forums, blog posts, undocumented
  one-off captures).
- **Tier F** — speculation, unverified.

### What this tier list tells you

A Tier-S claim has been observed on real hardware in this repository.
A Tier-A claim has a documented methodology you can re-run. A Tier-D
or Tier-F claim should be treated as a hypothesis until corroborated.
When this document quotes a protocol fact without an explicit tier,
the surrounding context (e.g. "verified on hardware", "per ndeadly")
identifies the tier. If you can't tell, treat the claim as unverified.

---

## Hardware: Joy-Con 2 BLE Protocol

### GATT Profile (discovered through BLE exploration during development)

The Joy-Con 2 uses a fully proprietary Nintendo GATT profile — no standard HID service.

```
SERVICE  00001800-...  (Generic Access)
SERVICE  00001801-...  (Generic Attribute)
SERVICE  [Nintendo custom]
  CHAR  ab7de9be-89fe-49ad-828f-118f09df7fd2  [notify]   ← Input Report 0x05 (common, mouse data ABSOLUTE)
  CHAR  649d4ac9-8eb7-4e6c-af44-1ea54fe5f005  [write]    ← Output Report (Command)
  CHAR  d5a9e01e-...        (JC-R)            [notify]   ← Input Report 0x08 (mouse data RELATIVE, smaller)
  CHAR  cc1bbbb5-...        (JC-L)            [notify]   ← Input Report 0x07 (mirror of 0x08 for left)
  CHAR  c765a961-...                          [notify]   ← Command Response #1 (fires on commands)
  CHAR  640ca58e-...        (JC-R only)       [notify]   ← Command Response #2
  CHAR  d3bd69d2-...                          [notify]   ← Unknown notify (purpose undocumented)
  CHAR  ab7de9be-...-fde                      [notify]   ← Unknown — CAUSES DISCONNECT if subscribed

DESCRIPTORS:
  679d5510-...  at handle 0x000c  ← "Set Report Rate?" sibling of 0x05  (write 0x85 0x00 = 200Hz)
  679d5510-...  at handle 0x0010  ← "Set Report Rate?" sibling of 0x07/0x08
```

(Cross-referenced with [ndeadly's `bluetooth_interface.md`](research/ndeadly_switch2/bluetooth_interface.md) which we cloned into `research/ndeadly_switch2/` for offline reference.)

**Critical finding:** No HID service (`0x1812`). This is why macOS caps the BLE connection interval at 30ms (~33Hz). Devices with `0x1812` are allowed 11.25ms (~88Hz).

**Earlier note about "silent characteristics" was wrong:** the `c765a961` / `640ca58e` / `d3bd69d2` characteristics are not auth-gated. They are documented by ndeadly as *Command Response* channels — they only fire when the host sends a command that requires a response, and they were silent in our earlier exploration because we never subscribed *and* never sent a triggering command in the same session. They do not carry sensor data; not useful for raising rate.

### Enabling Mouse Mode

Two writes to `WRITE_COMMAND_UUID` activate the optical sensor and
the rest of the feature stream:

```python
# Set Feature Mask  (cmd 0x0C, sub 0x02)
SET_MASK = bytes([0x0C, 0x91, 0x01, 0x02, 0x00, 0x04, 0x00, 0x00, 0xFF, 0x00, 0x00, 0x00])
# Enable Features   (cmd 0x0C, sub 0x04)
ENABLE  = bytes([0x0C, 0x91, 0x01, 0x04, 0x00, 0x04, 0x00, 0x00, 0xFF, 0x00, 0x00, 0x00])
```

Two correctness traps verified on hardware:

1. **Length byte (offset 5) must equal the actual payload length, not
   8.** Earlier code padded payloads to 8 bytes and reported `len=1`;
   the JC2 firmware silently rejected those. For feature-select the
   payload is 4 bytes, so length byte is `0x04`.
2. **Feature mask must be `0xFF`, not the trimmed `0x33` (Button +
   Stick + Mouse + Rumble) tried during earlier development.** The
   trim was theoretically correct per ndeadly's docs but the JC2
   firmware rejected it on real hardware (LEDs stayed in pairing-
   cycle mode, no vibration, mouse data zeroed). Reverted to `0xFF`
   to match the working `coffincolors/jc2mouse` Linux driver.

Without these, the controller still sends button data on input report
0x05 (buttons stream by default) but the optical mouse + IMU + battery
current fields stay zeroed.

### Input Report 0x05 Layout (`ab7de9be-…-7fd2`)

The full report layout, sourced from
`research/ndeadly_switch2/hid_reports.md` and cross-verified against
`research/coffincolors_jc2mouse/src/jc2mouse/driver.py` and
`Nadeflore/switch2-controllers/controller.py:125-140` (formerly cited as
`TropicalCyclone/switch2-controller-driver`, a fork that was deleted in 2026 —
same code, same line-by-line layout):

| Offset | Size | Content |
|---|---|---|
| 0x00 | 4 B | Counter (u32 LE, increments per report) |
| 0x04 | 4 B | Buttons (32-bit bitfield; we read 3 bytes per side at offsets 0x03/0x04 because side-specific bits don't overlap) |
| 0x08 | 2 B | Unknown |
| 0x0A | 3 B | Left stick (12-bit X, 12-bit Y packed) |
| 0x0D | 3 B | Right stick (12-bit X, 12-bit Y packed) |
| 0x10 | 8 B | Mouse data block — feature bit 4 |
| | 0x10 (2 B) | Optical sensor X (u16 LE, absolute) |
| | 0x12 (2 B) | Optical sensor Y (u16 LE, absolute) |
| | 0x14 (2 B) | Mouse roughness ("surface quality?" per ndeadly) |
| | 0x16 (2 B) | Mouse distance ("lift-off distance?" per ndeadly) |
| 0x18 | 1 B | Unknown (always 0) |
| 0x19 | 6 B | Magnetometer (3 × s16 LE, X/Y/Z) — feature bit 7 |
| 0x1F | 2 B | Battery voltage (u16 LE, mV) |
| 0x21 | 1 B | Charge state byte (0=on battery, 0x20=full, others=charging at rate) |
| 0x22 | 2 B | Battery current — **`raw / 100 = mA`** — feature bit 5 |
| 0x24 | 5 B | Unknown (always 0) |
| 0x29 | 1 B | Unknown (always 0x01) |
| 0x2A | 18 B | Motion Data block — feature bit 2 |
| | 0x2A (4 B) | Timestamp (u32 LE, **1 µs per tick = 1 MHz**) |
| | 0x2E (2 B) | Temperature (s16 LE, `°C = 25 + raw/127`) |
| | 0x30 (6 B) | Accelerometer X/Y/Z (s16 LE, `4096 raw = 1 G`) |
| | 0x36 (6 B) | Gyroscope X/Y/Z (s16 LE, `48000 raw = 360 °`) |

The optical sensor reports **absolute** position, not delta — delta is
computed in software via u16 wraparound arithmetic. Lift-off detection
checks the four bytes at 0x14–0x17 for all-zeros (the controller
zeroes the mouse data block when the sensor isn't on a surface).

**Empirical corrections to upstream docs** (verified on a JC2 (R)
over BLE on macOS, May 2026):

- Battery current at 0x22 was historically labelled "Battery
  Current?" by ndeadly with a question mark. We confirmed the **scale
  is `raw / 100 = mA`** — both via Nadeflore's working driver
  (`controller.py:138`, `self.battery_current = decodeu(data[33:35]) / 100`)
  and via 818 s of hardware capture (raw 1820 → 18.2 mA matches the
  525 mAh / 20-h spec).
- IMU timestamp scale was historically claimed by german77 to be a
  lower-rate `50,000-tick = 1-second` value, but our hardware capture
  proves it's **1 MHz** (1 µs/tick): observed Δts = 30000 across
  30 ms BLE packets matches 1 000 000 Hz, not the lower-rate figure.
  Possibly an over-USB-vs-BT difference; we record the BT-verified
  value.
- The 4-bit firmware-computed battery level (0..9) does **NOT** appear
  in input report 0x05. It's exclusive to the side-specific input
  reports (0x07, 0x08, 0x09, 0x0A) — see "Side-specific input reports"
  below.

### Side-specific input reports (0x07 / 0x08 / 0x09 / 0x0A)

Each Switch 2 controller variant has its OWN input report with a
side-specific GATT characteristic in addition to the common 0x05:

| Report | UUID | Controller |
|---|---|---|
| 0x07 | `cc1bbbb5-7354-4d32-a716-a81cb241a32a` | Joy-Con (L) |
| 0x08 | `d5a9e01e-2ffc-4cca-b20c-8b67142bf442` | Joy-Con (R) |
| 0x09 | (not in our GATT dump) | Pro Controller 2 |
| 0x0A | (not in our GATT dump) | NSO GameCube |

These reports carry overlapping but not identical fields vs 0x05.
Crucially, the side-specific reports include a **firmware-computed
battery level (0..9)** in their `Power Info` byte at offset 0x1 —
the same SoC value the Switch console reads. Layout per ndeadly's
`hid_reports.md`:

```
Offset 0x1, 1 byte, Power Info bitfield:
  bit 0      external power present
  bit 1      charging
  bits 2..5  battery level (0..9)
  bits 6..7  reserved
```

**Why we don't currently subscribe to these reports.** Hardware test
on JC2 (R) over BLE on macOS (May 2026): subscribing to a side-
specific report *kills* input report 0x05's stream. Constraint is
specific to two parallel input-report subscriptions — we tested
subscribing to 0x05 + a command-response notify channel
(`c765a961-…`) and that combination DOES coexist. So the platform
allows multiple notifies in general, but the JC2 firmware appears to
treat "console subscribed to side-specific report" as "switch
protocols, stop streaming common report."

Every working open-source driver we cross-checked
(`coffincolors/jc2mouse`, `Nadeflore/switch2-controllers`,
`TiernanDeFranco/JoyConPlusPlus`, `Misaka10571/joycon2-connector`)
subscribes to exactly ONE input report — they hit the same constraint.

**Path forward** (deferred): primary subscription switched from 0x05
to side-specific. Would gain firmware battery level, but lose: our
verified IMU calibration (Motion Data layout in side-specific reports
is "unknown packed format" per ndeadly), the magnetometer at offset
0x19 of 0x05, and the absolute-mouse path (0x07/0x08 carry relative
mouse only). Major refactor; not worth shipping for a percentage UI
hint when voltage approximation is honest enough.

### Tier-0 hardware-verified command responses

Empirically captured on JC2 (R) firmware **2.1.4.1** over BLE on macOS
(May 2026). All commands sent on `WRITE_COMMAND_UUID = 649d4ac9-…`,
responses arrived on the **BASIC** command-response channel
`c765a961-…` (handle 0x001A) — NOT the EXT-R channel `640ca58e-…`
(handle 0x001E) as `research/ndeadly_switch2/bluetooth_interface.md:415`
suggests. Worth flagging upstream as a doc clarification candidate.

Each response shares the standard 8-byte header: `[cmd_id, 0x01,
0x01, subcmd_id, 0x10, 0x78, 0x00, 0x00]`. The data follows.

| Request (8 B) | Response data | Decoded |
|---|---|---|
| `10 91 01 01 00 00 00 00` (Firmware Info / Get Version) | `02 01 04 01  0c 00 00 00  00 00 00 00` (12 B) | First 4 bytes = firmware version `2.1.4.1`. Next u32 = `12` (purpose unknown). Last 4 B padding. |
| `0b 91 01 03 00 00 00 00` (Get Battery Voltage) | `56 0d 00 00` (4 B) | u16 LE = `3414 mV`. Matches input report 0x05 offset 0x1F (read 3417 mV at the same instant — 3 mV difference is sensor noise). Confirms ndeadly's "same value as input report 0x05" claim. |
| `0b 91 01 04 00 00 00 00` (Get Charge Status) | `00 00 03 00` (4 B) | First u16 = charge state byte (`0x00` = on battery, matches input report 0x05 offset 0x21). Second u16 = unknown flags = `0x0003` (vs ndeadly's `0x0083` example while charging — high bit may signal "external power present"). |
| `0b 91 01 06 00 00 00 00` (Unknown) | `11 00 00 00` (4 B) | u32 LE = `0x00000011 = 17`. Matches ndeadly's documented constant exactly. Possibly a "controller present" sentinel. |

### "Set Report Rate?" descriptor read permissions

Three descriptors with UUID `679d5510-5a24-4dee-9557-95df80486ecb`
exist on JC2 — one per notify input characteristic. Handles observed
on JC-R: **`0x000c`, `0x0010`, `0x0028`**. All three are **write-only**
— attempting to read returns `GATT Protocol Error: Read Not Permitted`.
Earlier rate-descriptor experiments had successfully written
`0x85 0x00` to handle 0x000c without effect; this confirms why
introspection of the JC2's current rate-state setting via descriptor
read isn't possible.

### `d3bd69d2-…` notify channel — empirical observation

ndeadly tags this channel "Unknown notify (purpose undocumented)".
Empirical test: subscribe + issue all four documented battery /
firmware commands → channel stayed silent throughout. So the
`d3bd69d2-…` channel does NOT carry:

- spontaneous status streams (sensor data, etc.)
- responses to cmd 0x0B family (battery)
- responses to cmd 0x10 (firmware info)

It may fire on other paths (pairing handshake cmd 0x15, SPI flash
cmd 0x02, firmware OTA cmd 0x0D, etc.) — those weren't tested
(and `0x0D` deliberately wasn't, per its known disconnect side-effect).

### Write Command Format

```python
header = bytes([command_id, 0x91, 0x01, subcommand_id, 0x00, len(payload), 0x00, 0x00])
on_wire = header + payload   # exactly len(payload) bytes — NO PADDING
```

**Earlier versions zero-padded `payload` to 8 bytes minimum on the
theory that shorter payloads crashed the firmware (per moutella's
empirical comment). That's WRONG.** ndeadly's example responses for
LED commands (8 B payload), vibration presets (4 B payload), and
feature select (4 B payload) all show exact-length payloads. The
firmware silently rejects malformed commands where the length byte
disagrees with the actual payload size. This was the root-cause bug
behind the long-running "LEDs cycling on connect, no vibration, mouse
data zeroed" symptom seen during earlier development.

Caller must pass exactly the bytes the spec dictates per command.
The helpers `set_leds`, `play_vibration_preset`, `enable_mouse`,
`cancel_bluetooth_advertising` in `ble/protocol.py` handle the
padding-to-correct-length per command.

### Silent Characteristics (explored by probing unknown write UUIDs during development)

Three notify characteristics exist but never fire — assumed to require Nintendo authentication (challenge/response handshake not yet reverse-engineered). Writing every known probe to every unknown write UUID produced no activations.

---

## Why 33Hz and Not More

macOS enforces a **30ms minimum BLE connection interval** for non-HID peripherals (Apple QA1931). The connection interval is negotiated at the BLE link layer:

- **Joy-Con 2** (no `0x1812`) → macOS floor: 30ms → **~33Hz**
- **DualSense / Xbox** (has `0x1812`) → macOS floor: 11.25ms → **~88Hz**

### Attempted Workarounds (Approach C — forcing a faster BLE interval)

Explored private macOS APIs to force a faster interval:

- `IOBluetoothHostController.BluetoothHCILEConnectionUpdate_...` — exists, takes connection handle
- `CBCentralManager.setDesiredConnectionLatency_forPeripheral_` — exists but maps to Low/Medium/High enum, not ms values
- `CBCentralManager.retrieveConnectionHandleWithIdentifier_completion_` — can get HCI handle from CoreBluetooth UUID
- `CBPeripheral.enableFastLeConnection_withInfo_completion_` — private, discovered but result unknown

**Fundamental blocker:** Even if the HCI command is accepted, macOS enforces the 30ms policy at the OS scheduler level for non-HID devices. The Joy-Con firmware itself would also need to accept a faster interval in the BLE parameter negotiation.

### Approach D — "Set Report Rate?" descriptor

After cloning [ndeadly's research repo](https://github.com/ndeadly/switch2_controller_research) into `research/ndeadly_switch2/`, we discovered that each input characteristic has a sibling descriptor with UUID `679d5510-5a24-4dee-9557-95df80486ecb` ("Set Report Rate?" per ndeadly).

Decrypted nRF52840 captures of the Switch 2 console talking to the JC2 reveal a fixed pattern: **the console writes `0x85 0x00` to descriptor handle `0x0010`** (sibling of input report 0x07/0x08 at handle 0x000e), then enables notifications, and reports flow at **~200Hz** with a **5ms LL connection interval** (set via vendor-specific HCI command on the Switch's BT controller).

Replicated this exact procedure on macOS via bleak. Five test phases, each 6 seconds:

| Phase | Setup | Measured rate |
| ---   | ---   | --- |
| A | Subscribe input 0x05 (handle 0x000a), no rate write | 32.8 Hz |
| B | Same + write `0x85 0x00` to descriptor 0x000c | 33.2 Hz |
| C | Same + write `0x05/0x01/0xFF 0x00` | ~33 Hz (no change) |
| D | Subscribe input 0x07/0x08 (handle 0x000e), no rate write | 32.5 Hz |
| **E** | **Subscribe 0x000e + write `0x85 0x00` to handle 0x0010 (exact console replica)** | **32.3 Hz** |

**Result:** the descriptor write is accepted by the controller (no error response, no disconnect), but does not raise the reporting rate on macOS. Inter-packet `min` intervals occasionally drop to ~1ms, indicating macOS does deliver multiple PDUs per LL event sporadically — but as an exception, not the norm. Average rate stays pinned to ~33Hz.

**Verdict:** the descriptor is real and works on systems that allow shorter LL intervals (Linux/BlueZ). On macOS, the **30ms LL connection interval is enforced before any application-level descriptor can take effect**. No software-only workaround exists at this layer.

### Cross-platform comparison

| OS | Default (no API tweak) | With API tweak | Real rate measured |
| --- | --- | --- | --- |
| **macOS** | 30ms / 33Hz (verified) | No public/private API works (verified) | **33Hz** |
| **Windows 10/11** | 60ms / 16Hz (per ndeadly) | `BluetoothLEPreferredConnectionParameters.ThroughputOptimized` → **15ms / ~67Hz** ✅ | **~67Hz** |
| **Linux (BlueZ)** | Configurable | **5ms / 200Hz** via `gatttool` / libBlueZ HCI | 200Hz (per ndeadly captures) |

**Important honesty correction:** earlier versions of this doc claimed "7.5ms / 133Hz" on Windows, citing comments in [TheFrano/joycon2cpp](research/thefrano_joycon2cpp/) and [Misaka10571/joycon2-connector](research/misaka_joycon2_connector/). Those comments are **speculative** — both projects use the same `ThroughputOptimized` preset we use, and the WinRT preset is hard-coded to `min_connection_interval == max_connection_interval == 12 units` (12 × 1.25ms = **15ms**, ~67Hz). Verified by inspecting the live preset object on Windows. There is no public constructor for `BluetoothLEPreferredConnectionParameters` accepting custom values, so 15ms is the practical Windows ceiling for this API.

Sources for default-mode claim:
- ndeadly's [`bluetooth_interface.md`](research/ndeadly_switch2/bluetooth_interface.md) line 10 — Windows 60ms default in `bthport.sys`.

Sources for the 15ms ceiling (preset value):
- WinRT `BluetoothLEPreferredConnectionParameters.ThroughputOptimized` — runtime introspection on Windows 11 in this repo's exploration session.

**Implication:** Windows is still ~2× macOS, not ~4×. Linux is the only OS where the JC2 hits its native 200Hz; both macOS and Windows have OS-level floors enforced for non-HID-over-GATT BLE peripherals.

### Software-only options that remain on macOS

The only paths that would still help **without external hardware**, in decreasing pragmatism:

1. **IMU-based motion prediction** — each BLE packet bundles ~6 IMU samples (offset 0x2A in report 0x05; 0x10 in 0x07/0x08). Use the high-resolution IMU stream to predict cursor position between BLE packets. Reduces *perceived* latency without changing the rate. Real engineering, marginal gain in surface mode.
2. **Bumble + USB BT dongle** — [google/bumble](https://github.com/google/bumble) is a userspace BLE stack that drives USB BT adapters via libusb. Requires a non-Apple chipset USB BT dongle (CSR4.0/RTL8761) that macOS doesn't auto-claim. Mostly software but needs the hardware piece.
3. **External hardware relay** — nRF52840 firmware that talks to JC2 at 5ms and re-exposes it as USB-HID. Out of app scope but the only path to true 200Hz on Mac.

None of these are required for daily use; the current pump architecture's perceived latency (~40ms) is comparable to commercial BT mice.

---

## Software Architecture

After the modular blueprint refactor the codebase is layered, with each layer's allowed dependencies enforced by `import-linter` contracts in CI:

```
main.py              ── entry point, player lifecycle, __main__ block
├── tray.py          ── pystray icon construction (queue-only — no Tk calls)
├── bg_loop.py       ── singleton asyncio loop for fire-and-forget coros
├── joycon.py        ── JoyCon coordinator (delegates to parser + engine)
├── player.py        ── Player model, holds BleakClient(s) + gamepad
├── solo_logic.py    ── BLE notification dispatcher
│
├── ble/             ── BLE protocol layer
│   ├── constants.py     UUIDs, manufacturer ID, command/subcommand IDs
│   ├── feature_flags.py FEATURE_BUTTON / STICK / IMU / MOUSE / RUMBLE / ...
│   ├── protocol.py      write_command, set_leds, enable_mouse, vibration
│   └── connection.py    scan_device, connect_and_setup, reconnect loop
│
├── parser/          ── input report 0x05 decoders (stateless functions)
│   ├── constants.py     byte offsets (mouse / battery / etc.)
│   ├── button_masks.py  left/right Joy-Con button bitmask layout
│   ├── u16_delta.py     wrap-aware signed delta helper
│   ├── battery.py       voltage / charge / current → state.battery_*
│   ├── buttons.py       bitmask diff → InputSimulator click events
│   ├── mouse_optical.py absolute X/Y → wrap-aware delta + accel curves
│   └── sticks.py        12-bit packed stick → scroll accumulator
│
├── engine/          ── motion engine
│   ├── tuning.py        pump constants
│   └── motion_pump.py   async coroutine — drains accumulator @ display Hz
│
├── osio/            ── OS-specific I/O dispatchers (sys.platform-gated)
│   ├── boost.py         priority + anti-throttle + BLE rate negotiation
│   ├── mouse/           cursor injection (Quartz / SendInput)
│   └── hotkey/          global pause hotkey (CGEventTap / RegisterHotKey)
│
├── ui/              ── customtkinter dashboard (3 mixin classes)
│   ├── __init__.py      JoyglideUI = MRO of three mixins + ctk.CTk
│   ├── dashboard.py     DashboardMixin
│   ├── performance.py   PerformanceMixin
│   ├── settings_tab.py  SettingsMixin
│   └── modals/          accessibility prompt + joy-side picker
│
├── user_preferences.py  JSON-backed settings (platformdirs)
├── utils.py             decode_joystick + resource_path
└── applog.py            centralised logging configuration
```

Layered dependency directionality (enforced by `.importlinter`):

```
        main / tray / bg_loop
                ↓
     ble  ────  joycon  ────  ui
      ↓          ↓             ↓
              parser ── engine
                          ↓
                        osio    (leaf — no upward imports)
```

### Threading Model

```
Main thread (Tk/CTk mainloop)
  └─ ui.process_queue() every 100ms — reads command_queue, updates UI

BLE thread N (one per controller, asyncio event loop)
  └─ ble.connection.scan_device → connect_and_setup → handle_single_joycon
       └─ ble.connection.maintain_connection_loop (reconnect with exponential backoff)
       └─ BLE notification callback → solo_logic.handle_single_notification
            └─ JoyCon.process_{battery, buttons, mouse, sticks}
                  → parser.battery / buttons / mouse_optical / sticks
                       (mutate JoyCon state in place)
            └─ engine.motion_pump asyncio task (runs on same BLE event loop)
                 └─ osio.mouse.macos.CGEventPost / osio.mouse.windows.SendInput

bg_loop thread (singleton, daemon) — for fire-and-forget coros from
  non-async callers (tray menu items, hotkey, boot)

command_queue (thread-safe Queue) bridges BLE / hotkey / tray threads → Tk thread
```

### Settings Sharing

`user_preferences.settings` is a plain dict loaded once at import. BLE threads (via `parser.*.parse`) read it on every packet (~33-67 Hz). The Tk thread writes it on UI interaction and the tray-menu callbacks save via `bg_loop.save_settings_async()` (a daemon thread, never the AppKit/Tk main thread). No lock needed in practice because Python's GIL protects dict reads/writes at the bytecode level, and a momentarily stale value for a motion setting is harmless.

---

## Motion Pipeline

### Etapa por etapa

```
[Joy-Con optical sensor]
       │  absolute X/Y position, U16, updates at sensor native rate
       │
[BLE radio — 30ms interval]
       │  ~33Hz delivery to macOS CoreBluetooth
       │
[bleak callback — handle_single_notification]
       │  async, runs on BLE event loop thread
       │
[joycon.process_mouse()]
       │  1. Lift-off sentinel — OR the four bytes at 0x14-0x17
       │     (surface-quality + lift-off block); skip if all zero
       │     (firmware zeros the whole mouse block off-surface).
       │  2. Read raw X/Y U16
       │  3. _delta_u16(curr, prev) — signed delta with wraparound
       │  4. Apply deadzone (0 for Gaming, user-set for others)
       │  5. Apply acceleration multiplier (profile + accel_level)
       │  6. Apply sensitivity multiplier (global)
       │  7. Accumulate into _dx_accum / _dy_accum
       │
[_motion_pump — asyncio task at display Hz]
       │  runs at 60Hz (MacBook Air M4) or 120Hz (ProMotion)
       │  drain_factor:
       │    Gaming:   1.0        (full drain → 33Hz cursor updates, 1:1)
       │    Dynamic:  dt/0.030   (proportional → 60Hz smooth interpolation)
       │    Cinematic: 0.25      (slow drain → inertia/float effect)
       │  idle brake: keeps 30% per tick (70% decay) after 60ms of no motion
       │
[mouse.mouse_move(dx, dy)]
       │  updates cached _cx/_cy, clamps to screen bounds
       │
[CGEventPost(kCGHIDEventTap, CGEventCreateMouseEvent(...))]
       │  kernel-level injection — same pipeline as real hardware mouse
       ▼
[Cursor moves on screen]
```

### Latency Budget (worst case)

| Stage | Time |
|-------|------|
| Sensor → BLE packet | 0–30ms |
| BLE → CoreBluetooth → Python | ~2ms |
| process_mouse() | <0.1ms |
| Pump (wait for next tick) | 0–16.7ms at 60Hz |
| CGEventPost → visible frame | ~16.7ms |
| **Total worst case** | **~65ms** |
| **Total typical** | **~35–45ms** |

---

## Motion Profiles

### Gaming (Raw 1:1)
- `drain_factor = 1.0` — full accumulator drained on first pump tick after packet
- Deadzone = 0 — every sensor unit registers
- No acceleration curve
- Effective rate: 33Hz (BLE-limited)
- Use case: precision, FPS, low-latency tasks

### Dynamic (Smart Smooth) — Default
- `drain_factor = min(1.0, dt / 0.030)` — proportional to actual tick time
- At 60Hz display: ~56% drained per tick → movement spread across 2 pump ticks
- Acceleration curve (speed²-based): 1.0x at rest → up to 1.5x/2.5x/3.5x at speed
- Effective rate: 60Hz (pump-interpolated)
- Use case: productivity, general use

### Cinematic (Smooth/Inertia)
- `drain_factor = 0.25` — slow constant drain regardless of frame time
- Multiplier = 0.8 (slower, floatier)
- Idle brake decays accumulator smoothly when motion stops
- Use case: media, couch browsing, presentation

---

## Settings Reference

All settings persisted to JSON via `platformdirs` (macOS: `~/Library/Application Support/joyglide/settings.json`).

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `profile` | string | `"dynamic"` | `"dynamic"` / `"gaming"` / `"cinematic"` |
| `sensitivity` | float | `1.0` | Global movement multiplier (0.5–3.0) |
| `deadzone` | int | `2` | Optical sensor deadzone in raw units (0–8) |
| `disable_acceleration` | bool | `true` | Force raw 1:1 regardless of profile |
| `acceleration_level` | int | `2` | Accel curve intensity: 1=Low, 2=Med, 3=High |
| `scroll_sensitivity` | int | `4` | Stick scroll speed (1–10, normalized to 4=1.0x) |
| `double_click_enabled` | bool | `true` | Rapid press within 400ms = double-click |
| `vibration_on_connect` | bool | `true` | Haptic feedback when controller connects |
| `show_gatt_dump` | bool | `false` | Print full GATT profile to terminal on connect |
| `start_with_sync` | bool | `false` | Auto-start BLE scan on app launch |
| `ignore_opening_window` | bool | `false` | Start minimized to tray |
| `imu_enabled` | bool | `false` | Add `FEATURE_IMU` (bit 2) to the feature mask so the controller emits the 18-byte Motion Data block at offset 0x2A of input report 0x05 (timestamp + temp + accel + gyro). Powers future air-mouse mode. |
| `imu_dump_raw` | bool | `false` | Log decoded IMU values per packet — diagnostic / protocol verification. |
| `magnetometer_enabled` | bool | `false` | Parse the 6-byte magnetometer block at offset 0x19 of input report 0x05 and stash the raw `(x, y, z)` tuple on state. The mask bit is already set in `0xFF`, so this only gates per-packet parsing. |
| `magnetometer_dump_raw` | bool | `false` | Log raw magnetometer values per packet — diagnostic. |
| `battery_log` | bool | `false` | One-liner per 1 Hz tick logging mV / pct / mA. Current at offset 0x22 is hardware-verified `raw u16 / 100 = mA` (Tier S). |
| `motion_prediction_enabled` | bool | `false` | Pump extrapolates a small synthetic delta on ticks where no fresh BLE packet has arrived since the last tick, using previous BLE-frame velocity. Smooths visible cursor stepping when display refresh > BLE rate. |
| `devices` | dict | `{}` | Saved controller addresses → `{type: "left"/"right"}` |

---

## Native macOS Integration

### Event Injection (mouse.py)
Uses `Quartz.CoreGraphics` directly — no pynput, no virtual device:

```python
CGEventPost(kCGHIDEventTap, CGEventCreateMouseEvent(...))
```

`kCGHIDEventTap` is the lowest injection point — indistinguishable from hardware input to all apps. Requires Accessibility permission (`CGPreflightPostEventAccess()`).

Cursor position is cached (`_cx`, `_cy`) and updated locally, avoiding a `CGEventCreate` call on every movement tick. Position is re-synced from the real cursor only before click events (`_sync_pos()`).

### Anti App-Nap (osio/boost.py)
```python
NSProcessInfo.processInfo().beginActivityWithOptions_reason_(
    NSActivityUserInitiatedAllowingIdleSystemSleep | NSActivityLatencyCritical, "...")
```
Prevents macOS from throttling asyncio timers when the window is minimized, and `LatencyCritical` is Apple's flag for the highest timer / I-O availability (the "real-time audio" class) — it keeps the pump's ~16 ms deadlines out of timer coalescing. Idle system sleep is deliberately **allowed**: an earlier build passed the literal `0x00FFFFFF`, which is `NSActivityUserInitiated` *including* `IdleSystemSleepDisabled`, and silently kept the Mac awake. Use the named constants. (Tier B — Apple `NSProcessInfo` docs; constant values verified against pyobjc 12.2 on macOS 26.6.)

### Thread QoS (osio/boost.py + bg_loop.py)
```python
pthread_set_qos_class_self_np(QOS_CLASS_USER_INTERACTIVE, 0)   # on the bg-loop thread
```
The single background asyncio thread hosts every BLE callback and the motion pump. On Apple Silicon the QoS class is what keeps a thread on a performance core; a plain Python thread runs at `QOS_CLASS_DEFAULT` and can be parked on an efficiency core, which shows up as pump-tick jitter. `os.nice(-10)` is still attempted but always fails without root and never influenced core placement.

### Display Refresh Rate Detection (osio/mouse/macos.py)
`NSScreen.maximumFramesPerSecond` (AppKit, macOS 12+) — the maximum across all attached screens. It reports 120 on ProMotion panels where `CGDisplayModeGetRefreshRate()` returns 0 for adaptive-refresh built-in displays. Replaced a hard-coded list of ProMotion model identifiers that stopped at `Mac16,x`. Falls back to 60 Hz if no screen reports a plausible value.

### Synthetic mouse deltas (osio/mouse/macos.py)
`CGEventCreateMouseEvent` leaves `kCGMouseEventDeltaX/Y` at 0 (verified on macOS 26). Apps that read raw deltas instead of absolute position — FPS games with a captured cursor, `NSEvent.deltaX` consumers — saw no motion at all. Every Moved / Dragged event now carries the rounded per-event delta (same technique as Deskflow/Synergy).

### Hotkey event tap self-healing (osio/hotkey/macos.py)
WindowServer disables a `CGEventTap` whose callback is too slow (`kCGEventTapDisabledByTimeout`) and reports it by invoking the callback with that pseudo event type. A Python callback stalled by the GIL during a BLE burst is enough to trigger it; the callback now re-enables the tap instead of letting ⌃⌥M die silently.

---

## Complete Command ID Table

Sourced from `ndeadly/switch2_controller_research` and `german77/JoyconDriver` (Wireshark dissector).

| Command ID | Name | Key Subcommands |
|-----------|------|-----------------|
| `0x01` | NFC | `0x01`=Status, `0x02`=Read, `0x03`=Write |
| `0x02` | **Flash Memory (SPI)** | **`0x01`=Read, `0x02`=Write, `0x03`=Erase** |
| `0x03` | Initialization | `0x01`=Bluetooth Wake, `0x02`=Bluetooth Cancel, `0x08`=Clear pairing info (ndeadly commands.md, 2026-04 naming). ndeadly notes these "appear to be intended for use over USB/rail connections"; `0x02` nonetheless works over BLE — its LED effect is hardware-verified here (Tier S) |
| `0x08` | Charging Grip | `0x00`=Query, `0x01`=Enable |
| `0x09` | Player LEDs | `0x07`=Set pattern |
| `0x0A` | Vibration | `0x02`=Play preset, `0x08`=Raw LRA data |
| `0x0B` | Battery | `0x03`=Voltage, `0x04`=Charge status, `0x06`=Unknown (constant), `0x07`=Unknown. **No subcommand returns the firmware-computed 0..9 SoC level** — that field is exclusive to side-specific input reports. See "Tier-0 hardware-verified responses" below for actual wire bytes. |
| `0x10` | Firmware Info | `0x01`=Get Version (returns 12 B of data — see below) |
| `0x0C` | **Feature Select** | **`0x01`=Get info, `0x02`=Set mask, `0x03`=Clear mask, `0x04`=Enable, `0x05`=Disable** |
| `0x0D` | **Firmware Update (OTA)** | **`0x01`=Init, `0x04`=Data, `0x05`=Verify** |
| `0x15` | Bluetooth Pairing | `0x01`=Addr exchange, `0x02`=LTK confirm, `0x03`=Finalize |

**Critical:** Commands `0x0D` (OTA init) and `0x0E`/`0x0F` (undefined ranges) cause immediate BLE disconnection — the controller resets when it receives them. This was observed during SPI-dump exploration: testing those IDs crashed the connection.

### Command 0x0C (Feature Select) — payload is a feature-flag bitmask

`0x0C` doesn't take a "subcommand picks a feature" — the subcommand picks an *operation* (set mask, enable, disable, …) and the **payload** is a bitmask of which features the operation applies to. Set Feature Mask (`0x02`) **must** be called at least once before Enable/Disable can take effect.

| Bit  | Mask   | Feature       | Notes                                                                 |
|------|--------|---------------|-----------------------------------------------------------------------|
| 0    | `0x01` | Button state  |                                                                       |
| 1    | `0x02` | Analog sticks |                                                                       |
| 2    | `0x04` | IMU           | Linear accelerometer + gyro. Unused in this app.                      |
| 3    | `0x08` | (unused)      |                                                                       |
| 4    | `0x10` | Mouse data    | Optical sensor — Joy-Con only.                                        |
| 5    | `0x20` | Rumble        | **Also gates "Battery Current" reporting at offset `0x22` of 0x05.**  |
| 6    | `0x40` | (unused)      |                                                                       |
| 7    | `0x80` | Magnetometer  | Unused in this app.                                                   |

An earlier development build shipped `0x33` (Button + Stick + Mouse +
Rumble) on the theory that turning off the IMU + Magnetometer would
save controller-side battery. **Reverted to `0xFF`** after hardware
testing: the trimmed `0x33` mask was silently rejected by the JC2
firmware (LEDs stayed in pairing-cycle mode, vibration didn't fire,
mouse data block stayed zeroed). The `coffincolors/jc2mouse` Linux
driver — which works correctly — uses `0xFF`, and the reversion
matched theirs. The theoretical battery saving wasn't worth the
silent feature loss.

See `ble/feature_flags.py:FEATURE_MASK_DEFAULT`.

### Write Command Format Decoded

```
Byte 0:  Command ID    (e.g. 0x0C = Feature Select)
Byte 1:  0x91          = Direction: Host → Device
Byte 2:  0x01          = Transport: Bluetooth (0x00 = USB)
Byte 3:  Subcommand    (e.g. 0x02 = Set Feature Mask, 0x04 = Enable Features)
Byte 4:  0x00          = reserved
Byte 5:  Payload len   (REAL length of payload — NOT the padded length)
Byte 6–7: 0x00 0x00    = reserved
Byte 8+: Payload, EXACTLY len(payload) bytes — NO padding (firmware silently rejects packets where byte-5 length disagrees with on-wire payload size)
```

Reply packets have byte 1 = `0x01` (Device → Host) instead of `0x91`.

---

## Flash Memory Map (Joy-Con 2)

**Total flash: 2 MB.** Layout discovered via `ndeadly/switch2_controller_research`.

| Address Range | Size | Contents |
|--------------|------|----------|
| `0x00000–0x10FFF` | 0x11000 | Initial firmware (encrypted) |
| `0x11000–0x11FFF` | 0x1000 | Failsafe update pointer |
| `0x12000–0x12FFF` | 0x1000 | Failsafe magic trigger / firmware swap flag |
| `0x13000–0x14FFF` | 0x2000 | **Factory data** (serial, calibration) |
| `0x15000–0x74FFF` | 0x60000 | Failsafe firmware bank #1 |
| `0x75000–0xD4FFF` | 0x60000 | Failsafe firmware bank #2 |
| `0x175000–0x1F9FFF` | 0x85000 | DSP firmware |
| `0x1FA000–0x1FAFFF` | 0x1000 | **Bluetooth pairing info** (LTK + host addresses) |
| `0x1FC000–0x1FCFFF` | 0x1000 | **User calibration** (stick offsets) |

### Factory Data Region (`0x13000`)

| Offset | Size | Field |
|--------|------|-------|
| `0x13002` | 16B | Serial number |
| `0x13012` | 2B | Vendor ID |
| `0x13014` | 2B | Product ID |
| `0x130A8` | 9B | **Left stick factory calibration** |
| `0x130E8` | 9B | **Right stick factory calibration** |

Calibration format: `Magic (2B) + Center (6B) + Max (6B) + Min (6B)`

### Pairing Info Region (`0x1FA000`)

- Byte `0x1FA000`: host count (max ~6)
- Each entry: 0x28 bytes = host BT address (6B) + LTK key (16B) + padding

### Shipping Mode

- `0x1FD000`: all zeros = "virgin" / uninitialized; `0xFFFFFFFF` = provisioned

---

## Nintendo Pairing Handshake (command `0x15`)

This is the authentication gate that blocks the silent GATT characteristics. Not standard BLE SMP.

```
1. Console → Controller: subcommand 0x01 — sends two host BT addresses (16B)
2. Controller → Console: BT address response
3. Console computes: A1 (16B random public key)
4. Controller responds: B1 = fixed per-controller public key (hardcoded in firmware)
                        Example: 5CF6EE792CDF05E1BA2B6325C41A5F10
5. Both compute:  LTK = A1 XOR B1
6. Console → Controller: A2 (16B challenge)
7. Controller → Console: AES-128-ECB(LTK, reverse(A2)) — encrypted challenge response
8. Console verifies → sends subcommand 0x03 (finalize)
9. Controller stores LTK at 0x1FA000 in flash
```

Without completing this handshake, the 3 silent notify characteristics (`c765a961`, `640ca58e`, `d3bd69d2`) never fire.

---

## SPI Firmware Dump Attempts

Early SPI-dump exploration initially adapted the **Joy-Con 1** SPI read subcommand (`0x10` in Joy-Con 1 protocol) and tested wrong command IDs:

```
Tested (wrong): [0x01, 0x02, 0x08, 0x0B, 0x0D, 0x0E, 0x0F]
```

**Results:**
- `0x01`, `0x08`, `0x0B` — silent: 25–27 normal input packets, zero SPI replies
- `0x02` — also silent (correct SPI command ID but wrong subcommand `0x10` — should be `0x01`)
- `0x0D` — 1 packet then disconnect (**firmware OTA Init** command — triggered a controller reset)
- `0x0E` — immediate disconnect (undefined command range — controller resets)

**What the correct SPI read looks like** (from `ndeadly/switch2_controller_research`):

```python
# Correct Joy-Con 2 SPI Read:
# command_id = 0x02  (Flash Memory)
# subcommand = 0x01  (Read)
# payload    = [addr_le_4B, length_1B]

def build_spi_read_correct(addr: int, length: int) -> bytes:
    payload = struct.pack("<IB", addr, length).ljust(8, b'\x00')
    return bytes([0x02, 0x91, 0x01, 0x01, 0x00, len(payload), 0x00, 0x00]) + payload
```

Correct addresses to try (Joy-Con 2 specific, NOT the Joy-Con 1 addresses):

| Address | Length | Region |
|---------|--------|--------|
| `0x13002` | 16 | Serial number |
| `0x130A8` | 9 | Left stick factory calibration |
| `0x130E8` | 9 | Right stick factory calibration |
| `0x1FA000` | 0x28 | First paired host (BT address + LTK) |
| `0x1FC000` | 0x20 | User calibration |
| `0x1FD000` | 4 | Shipping mode flag |

**Status:** Correct command format now known. SPI read may still require the `0x15` pairing handshake to be completed first (auth gate), which is not yet implemented.

---

## Public Research Landscape

### What exists (as of May 2026)

| Project | Focus | Status |
|---------|-------|--------|
| [dekuNukem/Nintendo_Switch_Reverse_Engineering](https://github.com/dekuNukem/Nintendo_Switch_Reverse_Engineering) | Joy-Con **1** full protocol (BT Classic, SPI, HID subcommands) | Complete |
| [ndeadly/switch2_controller_research](https://github.com/ndeadly/switch2_controller_research) | Joy-Con **2** memory map, command IDs, pairing handshake | Active (~78 stars) |
| [german77/JoyconDriver](https://github.com/german77/JoyconDriver) | Wireshark Lua dissector for all Switch 2 controller protocols | Active |
| [darthcloud/BlueRetro](https://github.com/darthcloud/BlueRetro) | Bluetooth adapter; includes `sw2.c` with HID report structs | **Archived 2025-12-14** (`sw2.c` unchanged since 2025-07) |
| [coffincolors/jc2mouse](https://github.com/coffincolors/jc2mouse) | Joy-Con **2** Linux userspace BLE driver | Last commit 2026-03; open BlueZ GATT-discovery issues |
| [Nadeflore/switch2-controllers](https://github.com/Nadeflore/switch2-controllers) | Python Windows driver — source of the `raw / 100 = mA` battery-current cross-check (previously cited via the now-deleted TropicalCyclone fork) | Last commit 2025-11 |
| [TheFrano/joycon2cpp](https://github.com/TheFrano/joycon2cpp) | Windows C++ driver; v1.3 (2026-06) added HD rumble + a console-captured init sequence using feature mask **0x37** | Active |
| [OZORDI/JoyCon2Mac](https://github.com/OZORDI/JoyCon2Mac) | **macOS-native** CoreBluetooth + DriverKit driver (needs SIP/AMFI off). Uses mask `0xFF` and subscribes input 0x05 + response `c765a961` together — independent Tier-C corroboration of two findings in this doc | Created 2026-05, active |
| [NVNTLabs/Switch2-Mouse](https://github.com/NVNTLabs/Switch2-Mouse) | Hardware sniffing with Nordic nRF52840 | WIP, no code published |
| [CTCaer/jc_toolkit](https://github.com/CTCaer/jc_toolkit) | Joy-Con 1 + 2 calibration toolkit | Partial Joy-Con 2 support |

**Cross-validation:** `coffincolors/jc2mouse` independently arrived at the same UUIDs and enable commands — confirms our protocol is correct.

### What is NOT publicly documented (as of May 2026)

- Whether SPI read (command `0x02/0x01`) works without completing `0x15` pairing handshake first
- Purpose of the 3 silent notify characteristics (`c765a961-...`, `640ca58e-...`, `d3bd69d2-...`)
- Optical sensor chip manufacturer and native polling rate
- Whether Joy-Con 2 can negotiate BLE interval below 30ms at firmware level
- Magnetic docking protocol (rail attachment detection, commands)

No significant Joy-Con 2 reverse engineering activity found on Russian, Chinese, or underground hacking forums. Research is Western-concentrated (GitHub, GBAtemp, Reddit r/switchhacks).

---

## Known Limitations

1. **33Hz hardware ceiling** — The BLE connection interval cannot be reduced below 30ms on macOS without the Joy-Con implementing HID over GATT (`0x1812`). This is a firmware/OS policy constraint, not a software bug.

2. **Nintendo auth gates silent characteristics** — Three notify characteristics exist in the GATT profile but never activate. They likely require a Nintendo-proprietary authentication handshake to unlock (similar to how the Switch console authenticates Joy-Cons). Without this, their purpose is unknown.

3. **SPI flash inaccessible** — Joy-Con 2 does not respond to Joy-Con 1 SPI read subcommand `0x10` over GATT. Firmware dump is not possible with current public knowledge.

4. **One shared event loop for every controller** — `tray_connect_new_controller()` schedules the connect flow on the singleton background loop (`bg_loop`), and every player's BLE callbacks and pump task live there. One daemon thread hosts them all (and carries the USER_INTERACTIVE QoS); a stall in one controller's callback delays the others' pump ticks.

5. **asyncio.sleep jitter** — The pump uses `asyncio.sleep(1/Hz)` which is not a real-time timer. Under system load, ticks can slip 2–5ms. The Dynamic profile's `dt`-based `drain_factor` compensates for this automatically.

6. **No gyroscope/accelerometer for cursor** — The Joy-Con 2 IMU data
   is parsed (Motion Data block at offsets `0x2A..0x3B` of input
   report 0x05) and exposed on `JoyCon` state when
   `imu_enabled` setting is on, but the optical sensor alone drives
   cursor movement. IMU is currently scaffolding for future air-mouse
   / gesture features. Calibration scales (`4096 = 1 G`,
   `48000 = 360°`, `25 + raw/127 °C`) confirmed in
   github.com/german77/JoyconDriver#1 and verified on hardware
   (idle √(ax² + ay² + az²) ≈ 0.99 G).

---

## Exploration During Development

The protocol findings in this document were gathered through a series
of one-off BLE exploration sessions during development:

- **GATT discovery** — subscribing to all notify characteristics,
  measuring their Hz and capturing payloads.
- **Write probing** — writing probe commands to unknown write UUIDs
  to see whether any of the silent notify characteristics activate.
- **Interval forcing** — attempting to force a faster BLE connection
  interval via private macOS APIs (IOBluetooth + CoreBluetooth).
- **SPI dump attempts** — adapting the Joy-Con 1 SPI read subcommand
  to the Joy-Con 2 GATT format; confirmed non-functional.

These were throwaway scripts and are not part of the shipped codebase.

---

## Future Directions

| Idea | Feasibility | Notes |
|------|-------------|-------|
| Nintendo auth reverse engineering | Hard | Would unlock silent characteristics and potentially SPI flash access |
| Hardware BLE sniffer (nRF52840) | Medium | Capture raw air traffic between Switch 2 console and Joy-Con 2 to discover auth handshake |
| IOHIDUserDevice virtual driver | Medium | Create a virtual HID device fed by Joy-Con data — injects at 60Hz+ regardless of BLE rate |
| DriverKit extension | Hard | Proper kernel driver, would get HID treatment from OS |
| Gyroscope aiming mode | Easy | IMU data is already parsed at offsets 0x2A–0x3B (parser/imu.py); just needs a consumer in engine/ |
| Multi-controller support | Medium | Architecture supports it, needs player number UI and shared loop |
| Sensitivity per-axis | Easy | Split `sensitivity` into `sensitivity_x` / `sensitivity_y` in settings |
