# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-controller motion engine — composes the parsers and the pump.

After the modular blueprint refactor, this module is a thin coordinator:
state lives on ``JoyCon`` and behaviour delegates to ``parser.*`` and
``engine.motion_pump``.

  * ``parser.battery / buttons / mouse_optical / sticks`` own the
    per-field decoding of input report 0x05.
  * ``engine.motion_pump`` owns the asyncio task that drains the
    per-axis accumulator into actual cursor moves at display Hz.

The class itself just holds the state those modules read/mutate, plus
the lifecycle hooks (reset_state, set_side, start_pump). The public
methods (process_battery, process_mouse, etc.) are 1-line delegates so
existing callers (``solo_logic.handle_single_notification``, the test
suite) keep working unchanged.
"""
import asyncio
import time

from osio.mouse import InputSimulator
import parser.battery
import parser.buttons
import parser.imu
import parser.magnetometer
import parser.mouse_optical
import parser.power_info
import parser.sticks
from engine.motion_pump import motion_pump


class JoyCon:
    """Per-controller state holder + thin coordinator over parsers and pump.

    One instance per connected Joy-Con. Owns motion accumulators, battery
    state, the side flag (left/right), and the pump task handle. The
    public ``process_*`` methods are 1-line delegates to the ``parser.*``
    submodules; the actual decoding logic lives there. The motion pump
    runs as an asyncio task created by ``start_pump`` and lives in
    ``engine.motion_pump``.

    Threading: a JoyCon instance is created on the main thread but its
    accumulators are mutated from the BLE notification thread (via the
    parser delegates) and read from the per-controller asyncio loop
    (via the pump). The GIL is sufficient — accumulator updates are
    single bytecode ops, and a momentary stale value is harmless.
    """

    def __init__(self, side: str = "right") -> None:
        """Initialise a fresh JoyCon engine. Caller must invoke
        ``start_pump`` (idempotent — usually called from the parser
        when the first valid mouse packet arrives) before motion
        events propagate to the cursor."""
        self.input_simulator  = InputSimulator()
        self.side             = side
        self.is_left          = side != "right"
        self.last_mouse_pos: tuple[int | None, int | None]   = (None, None)
        self.last_data: bytes | None        = None
        self._dx_accum        = 0.0
        self._dy_accum        = 0.0
        self._scroll_x_accum  = 0.0
        self._scroll_y_accum  = 0.0
        self._last_motion_ts  = 0.0
        self._pump_task: asyncio.Task | None       = None
        self._pump_running    = False
        # Battery state — parsed from input report 0x05 (per ndeadly hid_reports.md
        # + german77 dissector). Voltage at 0x1F, charge state at 0x21, and
        # *current* at 0x22 (only populated when FEATURE_RUMBLE is enabled in
        # the feature mask, which we do by default). Updated at most once per
        # second to avoid UI churn.
        self.battery_mv: int | None        = None
        self.battery_pct: int | None       = None
        self.battery_current_ma: int | None = None
        self.battery_charging = False
        self.battery_full     = False
        self._battery_last_ts = 0.0
        # Firmware-computed battery level (0..9) — populated by
        # parser.power_info when we're subscribed to the side-specific
        # input report (0x07 JC-L / 0x08 JC-R). Same value the Switch
        # console reads. When set, parser.battery's voltage-derived
        # percentage is overridden by this firmware value.
        self.battery_level_raw:    int | None = None
        self.battery_external_power = False
        self.battery_pct_source: str = "voltage"  # "voltage" or "firmware"
        self._power_info_last_ts = 0.0
        # IMU state — populated by parser.imu when ``settings["imu_enabled"]``
        # is on. Calibration scales (4096 raw = 1 G accel, 48000 raw = 360°
        # gyro, 25 + raw/127 °C temperature) are firmware-defined and
        # confirmed in github.com/german77/JoyconDriver#1. Timestamp scale
        # is 1 MHz (1 µs/tick) — Tier S, hardware-verified on a JC2 (R) over
        # BLE on macOS (Δts ≈ 30000 per ~30 ms BLE packet = 1 MHz, not the
        # 50 kHz value cited in german77 issue #1 which likely refers to USB
        # or a different firmware revision).
        self.imu_timestamp:   int | None              = None
        self.imu_temperature: int | None              = None
        self.imu_temperature_c: float | None          = None
        self.imu_accel:       tuple[int, int, int] | None     = None
        self.imu_accel_g:     tuple[float, float, float] | None = None
        self.imu_gyro:        tuple[int, int, int] | None     = None
        self.imu_gyro_deg:    tuple[float, float, float] | None = None
        # Magnetometer state — populated by parser.magnetometer when
        # ``settings["magnetometer_enabled"]`` is on. Raw s16 counts;
        # absolute calibration needs SPI flash access (gated behind the
        # unimplemented Nintendo handshake).
        self.magnetometer:    tuple[int, int, int] | None     = None
        # Motion-prediction state — written by parser.mouse_optical when a
        # new BLE packet adds to the accumulator, decayed by the pump on
        # ticks that have no fresh data. Default zero so the predictor is
        # a no-op until real motion arrives.
        # Velocity is "delta per BLE packet" scaled to "delta per pump
        # tick" by the pump itself (see engine.predictor for the math).
        self._pred_vx: float = 0.0
        self._pred_vy: float = 0.0
        # Counter incremented on every fresh BLE optical-motion update,
        # stamped onto the pump's "last seen" tracker so the pump can
        # detect "no new data since last tick" without timestamp diffing.
        self._motion_seq: int = 0
        # Pause flag — toggled by the global hotkey. When True, all output is
        # suppressed but the BLE pipeline keeps running so resume is instant.
        self.paused           = False
        # Measured BLE packet period (EMA, in seconds). Drives the Dynamic
        # profile's drain_factor so it adapts to platform speed:
        #   macOS:   ~30ms  → drain ≈ 55% per pump tick at 60Hz display
        #   Windows: ~15ms  → drain ≈ 100% per pump tick (capped)
        # Initialized to 30ms (macOS-like worst case); converges in ~1s of motion.
        self._ble_period_ema  = 0.030
        self._last_packet_ts: float | None  = None

    def reset_state(self) -> None:
        """Wipe all motion state. Called on reconnect to avoid ghost cursor jumps
        that would otherwise come from stale accumulator/last-position values."""
        self.last_mouse_pos = (None, None)
        self.last_data      = None
        self._dx_accum      = 0.0
        self._dy_accum      = 0.0
        self._scroll_x_accum = 0.0
        self._scroll_y_accum = 0.0
        self._last_motion_ts = time.monotonic()

    def set_side(self, side: str) -> None:
        """Switch left/right at runtime without tearing down the engine.
        The pump task and InputSimulator stay alive; only the button mask
        and last_data are reset so the next packet's button diff is clean."""
        self.side    = side
        self.is_left = side != "right"
        self.last_data = None

    def _on_pump_done(self, _task: "asyncio.Task") -> None:
        """Pump-task done callback — clears the running flag so a future
        ``start_pump`` call recreates the task instead of no-op'ing."""
        self._pump_running = False

    def start_pump(self) -> None:
        """Start the motion pump on the current event loop. Idempotent."""
        if self._pump_running:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._pump_running = True
        self._pump_task = loop.create_task(motion_pump(self))
        self._pump_task.add_done_callback(self._on_pump_done)

    def track_packet_rate(self) -> None:
        """Update the BLE packet-rate EMA. Call once per BLE notification.

        Tracks the live inter-packet interval as an EMA, smoothed by 0.85.
        The pump's Dynamic-profile drain_factor reads from ``_ble_period_ema``
        and auto-adapts to whatever rate the OS actually delivered: ~30 ms
        on macOS, ~15 ms on Windows with ThroughputOptimized.

        Important: this runs on EVERY packet, not just packets that contain
        valid optical-sensor data. If we only tracked when the JC was on a
        surface, lifting it for a few seconds would freeze the EMA and the
        first move after putting it back down would feel laggy until the
        EMA recovered.
        """
        now_packet = time.monotonic()
        if self._last_packet_ts is not None:
            interval = now_packet - self._last_packet_ts
            # Reject implausibly slow gaps (BLE hiccup, CPU stall, reconnect)
            # so the EMA tracks the steady-state rate, not pathological events.
            if 0.005 <= interval <= 0.100:
                self._ble_period_ema = 0.85 * self._ble_period_ema + 0.15 * interval
        self._last_packet_ts = now_packet

    # ── Parser delegates — public API preserved for backward compat ────────
    def process_battery(self, data: bytes) -> None:
        """Parse battery voltage / charge state / current from input report
        0x05. Delegates to ``parser.battery``."""
        parser.battery.parse(self, data)

    def process_buttons(self, data: bytes) -> None:
        """Diff button bitmask vs the previous packet, emit click events
        on L/R/ZL/ZR transitions. Delegates to ``parser.buttons``."""
        parser.buttons.parse(self, data)

    def process_mouse(self, data: bytes) -> None:
        """Decode the optical sensor's absolute X/Y, compute the wrap-aware
        delta vs the previous sample, push into the motion accumulator.
        Delegates to ``parser.mouse_optical``."""
        parser.mouse_optical.parse(self, data)

    def process_sticks(self, data: bytes) -> None:
        """Decode the analog stick (12-bit packed), apply the profile-aware
        scroll curve, push into the scroll accumulator. Delegates to
        ``parser.sticks``."""
        parser.sticks.parse(self, data)

    def process_imu(self, data: bytes) -> None:
        """Decode the Motion Data block (gyro + accel + temp + timestamp).
        No-op when ``settings["imu_enabled"]`` is False. Delegates to
        ``parser.imu``."""
        parser.imu.parse(self, data)

    def process_magnetometer(self, data: bytes) -> None:
        """Decode the 6-byte magnetometer block (3 × s16 LE) at
        offset 0x19. No-op when ``settings["magnetometer_enabled"]``
        is False. Delegates to ``parser.magnetometer``."""
        parser.magnetometer.parse(self, data)

    def process_power_info(self, data: bytes) -> None:
        """Decode the Power Info bitfield at offset 0x1 of the
        side-specific input report (0x07 JC-L / 0x08 JC-R). Updates
        battery percentage from the firmware's own SoC estimation,
        overriding the voltage approximation. Delegates to
        ``parser.power_info``."""
        parser.power_info.parse(self, data)
