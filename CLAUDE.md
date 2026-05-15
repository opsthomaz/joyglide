# joyglide — Engineering rules

This file is read at the start of every Claude Code session in this
repo (and is compatible with other AI tools that respect `CLAUDE.md`
conventions like Cursor). Merge with the user's global instructions.

---

## Foundation: Karpathy guidelines

The project follows the four behavioral principles from the
[`karpathy-guidelines`](https://github.com/forrestchang/andrej-karpathy-skills)
plugin. Summary:

1. **Think Before Coding** — surface assumptions, ask if unclear,
   present alternatives instead of picking silently.
2. **Simplicity First** — minimum code that solves the problem,
   nothing speculative. No abstractions for single-use code. No
   error handling for impossible scenarios. If a senior engineer
   would call it overcomplicated, simplify.
3. **Surgical Changes** — touch only what you must. Don't refactor
   adjacent code, don't fix unrelated style. Match existing
   conventions even if you'd write them differently. Every changed
   line should trace to the user's request.
4. **Goal-Driven Execution** — define verifiable success criteria
   before implementing. "Add validation" → "write tests for invalid
   input, then make them pass." Strong success criteria let you
   verify independently.

---

## Project-specific rules

### 1. Protocol claims (this is reverse-engineered BLE)

The Joy-Con 2 BLE protocol is community-reverse-engineered. Every
claim about offsets, command IDs, response formats, or calibration
constants has a tier rating in the project's hierarchy
(see `docs/ARCHITECTURE.md` "What this tier list tells you" or
the formal definitions earlier in that doc):

- **Tier S** = empirical convergence (multiple sources agree AND
  hardware-tested on a real JC2 in this repo)
- **Tier A** = methodical reverse engineering with traceable
  methodology (ndeadly's pcaps, german77's dissector)
- **Tier B** = official platform docs (Apple QA1931, Microsoft Learn,
  switchbrew.org)
- **Tier C** = working third-party driver code (trust the code, not
  the comments)
- **Tier D** = community sources (forums, blog posts)
- **Tier F** = speculation, unverified

**Rules:**

- Never claim a protocol fact without a tier rating. If you don't
  know the tier, the answer is "unverified" — that's a real answer.
- For any non-trivial protocol claim, use the verification subagents:
  - `/protocol-verifier` — full-stack one-shot
  - `/ndeadly-checker` + `/driver-evidence` — parallel swarm
    (better signal than the one-shot agent)
  - `/claim-supporter` + `/claim-skeptic` — adversarial pair for
    high-stakes calls (e.g. "is this Tier S or Tier B?")
- Hardware-validated (Tier S) trumps documented (Tier A) trumps
  speculation. Don't ship code based on a single Tier-D source.

### 2. Architectural invariants (enforced by `import-linter`, hard-fail in CI)

- `parser/` must NOT depend on `ui/` or `ble/`.
- `engine/` must NOT depend on `ble/` or `parser/`.
- `osio/` is a leaf — no first-party imports back upward.
- `ui/` must NOT import `ble/` directly. Cross-thread comms goes
  through `command_queue` (a stdlib `queue.Queue`).
- See `.importlinter` for the exact 5 contracts.

### 3. BLE protocol invariants (do NOT regress these — verified on hardware)

These are the bugs that hardware validation surfaced. They are
counterintuitive enough that AI tools have re-introduced them
multiple times. The CLAUDE.md exists in part to prevent that.

- **`write_command` sends EXACTLY `len(payload)` bytes — no padding.**
  v0.6.0 fixed a long-standing bug where short payloads were
  zero-padded to 8 bytes while the header reported `len=1`. The JC2
  firmware silently rejected the malformed commands. The
  "8-byte minimum" comment from moutella's original code was wrong.
- **`FEATURE_MASK_DEFAULT = 0xFF` — do NOT trim.** v0.2.12 trimmed
  to `0x33` (Button + Stick + Mouse + Rumble) on the theory it
  saved controller-side battery; v0.6.0 reverted after hardware
  testing proved the firmware silently rejected it. The
  `coffincolors/jc2mouse` Linux driver uses `0xFF` and that's the
  empirically-validated path.
- **Subscribing to two input-report characteristics simultaneously
  kills input report 0x05's stream — but ONLY on the same
  peripheral.** Verified: with two JC2s connected (JC-R + JC-L),
  each peripheral's input report 0x05 stream coexists with the
  other's command-response subscriptions cleanly. The constraint is
  per-peripheral, not per-host. So:
  - Don't add a side-specific subscribe alongside the common one
    on a single JC.
  - DO connect multiple JCs to the same host — each is independent.
  Command-response notify channels (`c765a961-…`, `640ca58e-…`)
  also coexist with input report 0x05 on the same peripheral
  (v0.7.0 verified).
- **`start_notify` must be idempotent.** bleak raises
  `BleakError("Characteristic notifications already started")` on
  spurious reconnect callbacks (macOS BT stack quirk). See
  `main.handle_single_joycon` for the catch-and-skip pattern.
- **Reconnect path requires a fresh `BleakClient`.** After macOS BT
  is toggled off, the existing client is poisoned (CBInternalErrorDomain
  Code=32 "Local device is powered off"). See
  `ble.connection.maintain_connection_loop` — recreate per retry.
- **`post_connect_setup` sends Bluetooth Cancel before set_leds.**
  ndeadly notes the firmware quirk that LEDs keep cycling after
  advertising stops. Without this command, reconnect via sync
  button leaves LEDs in pairing-mode visual state.
- **macOS `kCGMouseEventClickState` must be set to ≥1 on Down/Up
  events**, not just Move. RightMouse and OtherMouse events
  specifically fail to register as context-menu-triggering clicks
  without it (v0.7.0 fix).

### 4. Calibration constants (Tier S — hardware-verified)

- IMU accel: `4096 raw counts = 1 G`
- IMU gyro: `48000 raw counts = 360°`
- IMU temperature: `°C = 25 + raw / 127`
- IMU timestamp: **`1 MHz` (1 µs/tick)** — NOT 50 kHz as
  github.com/german77/JoyconDriver#1 states. We measured Δts=30000
  across 30ms BLE packets which equals 1 MHz, not 50 kHz. Possibly
  the issue's comment refers to USB or a different revision; the
  BLE-on-macOS verified value is 1 MHz.
- Battery current: **`raw u16 / 100 = mA`** (TropicalCyclone driver
  + our 818-s capture both confirm: raw 1820 → 18.2 mA matches the
  525 mAh / 20-h spec).

### 5. Testing rules

- New parsers in `parser/` get:
  - A property test in `tests/test_property.py` (Hypothesis,
    explores edge cases including denormals)
  - At least one exact-value test pinning a known-good case
- New protocol-related code: Tier rating in commit message or
  docstring.
- Hardware-only verifications go in `CHANGELOG.md` with date +
  observed values, marked Tier S.
- "Passes" = all of:
  - 130+ tests green (currently 130; growing)
  - `ruff check .` clean
  - `pyright` 0 errors / 0 warnings
  - `lint-imports` 5 / 5 contracts
  - `xenon` under cap (max C / avg A)
- Never commit with red gates.

### 6. When in doubt

- Use the verification subagents (`.claude/agents/`):
  - `protocol-verifier` — quick one-shot
  - `ndeadly-checker` + `driver-evidence` — parallel swarm
  - `claim-supporter` + `claim-skeptic` — adversarial pair
- Cross-check against `docs/ARCHITECTURE.md` and `docs/RESEARCH.md`
  for context on past decisions and what hardware tests showed.
- Match the existing module pattern (`parser/`, `ble/`, `engine/`,
  `osio/`, `ui/`, plus the flat-root coordinator files).

### 7. Don't break the user's hardware

- Cmd `0x0D` (Firmware Update — OTA) causes immediate disconnect
  per SPI-dump exploration during development. **Do NOT send it
  without explicit user authorisation.** OTA-related commands could
  potentially brick the JC2.
- Cmd `0x15` (Pairing Handshake) writes pairing info to flash at
  `0x1FA000`. Sending another console's LTK (e.g. one extracted from
  a different driver) may break the user's bond with their real
  Switch 2. Do NOT implement without a sacrificial test JC2.

---

**These rules exist to prevent regressions of hardware-verified
fixes and to enforce the project's tier-based discipline on
protocol claims. Working code and green gates are necessary but
not sufficient — the rules above are the additional guard.**
