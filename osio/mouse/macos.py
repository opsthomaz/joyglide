# SPDX-License-Identifier: GPL-3.0-or-later
"""macOS backend for cursor injection.

Uses ``Quartz.CoreGraphics`` directly (the lowest public injection point on
macOS) — events posted via ``CGEventPost(kCGHIDEventTap, ...)`` are
indistinguishable from real hardware input to userspace apps.

Key choices:

  * ``CGEventCreateMouseEvent`` accepts floating-point ``CGPoint`` coords,
    so we keep the cursor position as ``float`` and let macOS render
    sub-pixel motion natively (no software accumulator needed, unlike the
    Windows backend).
  * The cached ``_cx``/``_cy`` lets us avoid a ``CGEventCreate`` call on
    every move tick. We re-sync from the real cursor only before clicks.
  * ``CGDisplayBounds`` is read in **points** (not pixels) — required, because
    on Retina displays pixels = 2× points and clamping at pixel dims would
    push the cursor 2× past the screen, breaking the Dock auto-show hot zone.
  * Display refresh rate comes from ``NSScreen.maximumFramesPerSecond``
    (macOS 12+), which reports the panel's true peak — 120 on ProMotion —
    where ``CGDisplayModeGetRefreshRate`` returns 0 for adaptive-refresh
    built-in displays.

Requires Accessibility permission. ``check_accessibility()`` returns the
current state; the app uses it to gate startup and prompt the user if
denied.
"""
import time
from Quartz import CoreGraphics as CG
from user_preferences import settings
from applog import get_logger
import latency_trace

log = get_logger(__name__)


def _max_refresh_rate_across_displays() -> float:
    """Return the highest refresh rate among all currently active displays.

    Multi-monitor setups can mix refresh rates (e.g. a 60Hz built-in panel
    plus a 120Hz external). The pump runs at a single rate, so picking the
    MAX guarantees we keep up with the fastest panel — running faster than
    the slowest panel just means a few wasted ticks, but running slower
    than the fastest means visible stutter on the fast one.

    ``NSScreen.maximumFramesPerSecond`` (AppKit, macOS 12+) is the API
    Apple provides for exactly this; it reports 120 on ProMotion panels
    where ``CGDisplayModeGetRefreshRate`` returns 0. Earlier builds fell
    back to a hard-coded list of ProMotion model identifiers, which went
    stale with every new MacBook Pro generation. Falls back to 60 Hz when
    no screen reports a plausible value.
    """
    try:
        from AppKit import NSScreen
        rates = [float(s.maximumFramesPerSecond()) for s in NSScreen.screens()]
    except Exception as e:
        log.debug(f"NSScreen refresh-rate query failed: {e}")
        rates = []
    rates = [r for r in rates if 30.0 <= r <= 500.0]
    return max(rates) if rates else 60.0


def _screen_bounds():
    """Return ``(width, height, refresh_rate)`` for the *primary* display.

    Width/height are in **points** (logical), not pixels. ``CGDisplayBounds``
    returns the display rect in points; using pixel dimensions here would 2×
    the clamp range on Retina and place the cursor far past the actual
    screen, which breaks bottom-edge hot zones (the Dock auto-show, in
    particular) from ever firing.

    The refresh rate is the **maximum** across all connected displays, not
    just the primary — we want the pump fast enough for the fastest panel
    in case the user moves windows around.
    """
    display_id = CG.CGMainDisplayID()
    bounds = CG.CGDisplayBounds(display_id)
    width  = float(bounds.size.width)
    height = float(bounds.size.height)
    refresh_rate = _max_refresh_rate_across_displays()
    return width, height, refresh_rate


_MOVE_EVENT_TYPES = (CG.kCGEventMouseMoved,
                     CG.kCGEventLeftMouseDragged,
                     CG.kCGEventRightMouseDragged,
                     CG.kCGEventOtherMouseDragged)


def _post(event_type, pos, button=CG.kCGMouseButtonLeft, click_count=1, *,
          delta: "tuple[float, float] | None" = None):
    """One-shot helper: build a CGEventMouseEvent and post it on the
    HID event tap (the lowest injection point — same path real input
    devices take, so apps can't tell our events from hardware).

    ``delta`` (dx, dy) is written into ``kCGMouseEventDeltaX/Y`` for
    Moved / Dragged events. ``CGEventCreateMouseEvent`` leaves those
    fields at 0 (verified on macOS 26), and consumers that read raw
    deltas rather than absolute position — FPS games with a captured
    cursor, ``NSEvent.deltaX`` users — saw no motion from Joyglide at
    all. The fields are integer-valued, so the float delta is rounded to
    nearest (not truncated) so sub-pixel moves still register once they
    cross ±0.5 px. Same technique as Deskflow/Synergy's synthetic
    relative moves.

    Always sets ``kCGMouseEventClickState`` for Down/Up events. Earlier
    versions only set it when ``click_count > 1``, leaving the field at
    0 for single clicks. Most macOS receivers tolerate that for
    LeftMouse events (since the standard down/up sequence is enough to
    classify the click), but RightMouse events on some app surfaces
    require an explicit click state of ≥1 to be interpreted as a
    "context-menu-triggering click" rather than a generic mouse-down
    notification. Hardware verification on a JC2 (R) over BLE on
    macOS 14: ZR was firing the RightMouseDown event but Finder /
    browsers weren't bringing up the context menu. Setting the field
    unconditionally fixed it.

    Optional latency instrumentation (``settings["latency_trace"]``):
    when on, ``time.perf_counter_ns()`` is taken just before and just
    after ``CGEventPost`` and ``latency_trace.record`` is called with
    the two derived spans. Off by default — the one ``settings.get``
    check is the only hot-path cost when off.
    """
    trace = settings.get("latency_trace", False)
    t1 = time.perf_counter_ns() if trace else 0

    ev = CG.CGEventCreateMouseEvent(None, event_type, pos, button)
    # Down/Up events get the click state. Move/Drag/Scroll events
    # don't — setting it on those is a no-op but skipping it keeps
    # the on-wire payload minimal.
    if event_type in (CG.kCGEventLeftMouseDown,  CG.kCGEventLeftMouseUp,
                      CG.kCGEventRightMouseDown, CG.kCGEventRightMouseUp,
                      CG.kCGEventOtherMouseDown, CG.kCGEventOtherMouseUp):
        CG.CGEventSetIntegerValueField(ev, CG.kCGMouseEventClickState, max(1, click_count))
    elif delta is not None and event_type in _MOVE_EVENT_TYPES:
        CG.CGEventSetIntegerValueField(ev, CG.kCGMouseEventDeltaX, round(delta[0]))
        CG.CGEventSetIntegerValueField(ev, CG.kCGMouseEventDeltaY, round(delta[1]))
    CG.CGEventPost(CG.kCGHIDEventTap, ev)

    if trace:
        t2 = time.perf_counter_ns()
        latency_trace.record("cgevent_us", t2 - t1)
        # Synchronous Gaming-profile path only: t0 was set by
        # solo_logic.handle_single_notification, parser/mouse_optical
        # is calling _post within the same call stack, so the
        # contextvar is still visible. In Dynamic/Cinematic the pump
        # task is a separate coroutine and the contextvar default 0
        # makes this branch a no-op (we don't have an honest t0 there).
        t0 = latency_trace.bleak_callback_start_ns.get()
        if t0:
            ctx = {"profile": settings.get("profile", "dynamic")}
            latency_trace.record("internal_us", t1 - t0, ctx)
            # Reset so a downstream _post call from the same callback
            # (e.g. mouse_move immediately followed by mouse_down)
            # doesn't double-count an earlier t0.
            latency_trace.bleak_callback_start_ns.set(0)


def _post_cmd_key(keycode):
    """Synthesise an atomic ``Cmd + <keycode>`` keystroke (down + up).

    Used for back / forward navigation on macOS — see the comment on
    ``InputSimulator.mouse_down_back`` for why we don't use mouse
    buttons. Quartz needs the Command modifier flag set on BOTH the
    key-down and key-up events for the receiving app's keystroke
    handler to register the chord correctly.
    """
    flags = CG.kCGEventFlagMaskCommand
    for is_down in (True, False):
        ev = CG.CGEventCreateKeyboardEvent(None, keycode, is_down)
        CG.CGEventSetFlags(ev, flags)
        CG.CGEventPost(CG.kCGHIDEventTap, ev)


def check_accessibility() -> bool:
    """Pure read of the TCC state — no side effects, safe to poll."""
    return CG.CGPreflightPostEventAccess()


def request_accessibility() -> None:
    """Register this app's signature with TCC so it shows in the Accessibility list.

    A passive ``CGPreflightPostEventAccess`` will return False forever if the
    app's bundle id + code-signature pair has never been authorized — and
    crucially, the user has no way to enable it because the entry simply
    doesn't appear in System Settings → Privacy & Security → Accessibility.

    The two calls below trigger the registration:
      * ``CGRequestPostEventAccess`` — adds the entry to the post-events
        access list (the one CGEventPost actually consults).
      * ``AXIsProcessTrustedWithOptions({prompt: True})`` — adds it to the
        Accessibility list AND fires the standard "X needs Accessibility
        access" system prompt, with a button that opens the right pane.

    Both are no-ops when the app is already trusted. Safe to call on every
    launch, and necessary to call at least once so the user has something
    to toggle.
    """
    try:
        CG.CGRequestPostEventAccess()
    except Exception as e:
        log.debug(f"CGRequestPostEventAccess failed: {e}")
    try:
        from ApplicationServices import (
            AXIsProcessTrustedWithOptions,
            kAXTrustedCheckOptionPrompt,
        )
        AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
    except Exception as e:
        log.debug(f"AXIsProcessTrustedWithOptions failed: {e}")

class InputSimulator:
    """macOS cursor injection backend via Quartz CoreGraphics.

    Holds the cached cursor position, button-state flags, and screen
    dimensions used to clamp motion. Exposes the platform-agnostic API
    that ``parser.mouse_optical`` and ``parser.buttons`` call:
    ``mouse_move``, ``mouse_down/up``, ``mouse_down_right/up_right``,
    ``mouse_double_click``, ``mouse_scroll``.
    """

    def __init__(self) -> None:
        """Probe the current cursor position + screen bounds + display
        refresh rate. Cached values are used by the hot-path
        ``mouse_move`` to avoid a CGEvent allocation per tick."""
        self._left_down  = False
        self._right_down = False
        self._last_click_time = 0.0
        self._last_click_x = 0.0
        self._last_click_y = 0.0
        self._click_count = 1

        # Cache initial cursor position. Avoids a CGEventCreate call per
        # mouse_move tick on the hot path — we keep _cx/_cy in sync as we
        # emit events, only re-syncing from the real cursor before clicks.
        p = CG.CGEventGetLocation(CG.CGEventCreate(None))
        self._cx = p.x
        self._cy = p.y

        sw, sh, hz = _screen_bounds()
        self._sw = float(sw)
        self._sh = float(sh)
        self.refresh_rate = float(hz)
        log.info(f"🖥️  Display Detected: {int(sw)}x{int(sh)} @ {hz}Hz")

    def _sync_pos(self) -> None:
        """Pull the real cursor position into our cache.

        Call before emitting click events so the click lands where the user
        actually sees the cursor, not where our internal cache thinks it is
        (caches can drift if the cursor was moved by the user / another app).
        """
        p = CG.CGEventGetLocation(CG.CGEventCreate(None))
        self._cx = p.x
        self._cy = p.y

    def _clamp(self) -> None:
        """Pin the cached cursor to the screen rectangle so motion past
        the edge doesn't accumulate off-screen position that would have
        to be 'undone' before the cursor visibly responds again."""
        if self._cx < 0.0: self._cx = 0.0
        elif self._cx > self._sw: self._cx = self._sw
        if self._cy < 0.0: self._cy = 0.0
        elif self._cy > self._sh: self._cy = self._sh

    def mouse_move(self, dx: float, dy: float) -> None:
        """Apply a sub-pixel relative motion. If a button is held, emits a
        Drag event of the matching kind so apps see proper drag-while-
        held semantics; otherwise a plain MouseMoved."""
        self._cx += dx
        self._cy += dy
        self._clamp()
        pos = (self._cx, self._cy)
        delta = (dx, dy)
        if self._left_down:
            _post(CG.kCGEventLeftMouseDragged, pos, delta=delta)
        elif self._right_down:
            _post(CG.kCGEventRightMouseDragged, pos, CG.kCGMouseButtonRight, delta=delta)
        else:
            _post(CG.kCGEventMouseMoved, pos, delta=delta)

    def mouse_down(self) -> None:
        """Press the left mouse button. Re-syncs position from the real
        cursor first so the click lands where the user sees the cursor.
        Bumps the click counter (1 → 2 → 3, capped) when consecutive
        clicks fall within the double-click window (400ms by default)."""
        self._sync_pos()
        self._left_down = True

        # ``time.monotonic`` (not ``time.time``) — wall-clock can step
        # backwards on NTP adjustment, which would break the 400 ms
        # double-click window (either by classifying a real double-click
        # as a single, or by treating two clicks 10 s apart as one
        # double on a -10 s NTP step).
        now = time.monotonic()
        # Native macOS double-click detection checks BOTH time and
        # position (NSEvent.mouseSlopForDoubleClick ≈ a few pixels).
        # Two clicks within 400ms but at different positions — e.g.
        # tab A then tab B in Firefox — must be classified as two
        # SINGLE clicks, not as a double-click. Otherwise the second
        # click arrives with click_count=2 and the receiving app
        # interprets it as a double-click on the wrong target
        # (Firefox tab activation breaks; menus dismiss strangely).
        # 5 px tolerance matches Apple's default mouse-slop.
        same_spot = (abs(self._cx - self._last_click_x) <= 5
                     and abs(self._cy - self._last_click_y) <= 5)
        if (settings.get("double_click_enabled", True)
                and same_spot
                and now - self._last_click_time < 0.4):
            self._click_count = min(3, self._click_count + 1)
        else:
            self._click_count = 1
        self._last_click_time = now
        self._last_click_x = self._cx
        self._last_click_y = self._cy

        _post(CG.kCGEventLeftMouseDown, (self._cx, self._cy), CG.kCGMouseButtonLeft, self._click_count)

    def mouse_up(self) -> None:
        """Release the left mouse button. Carries the same click counter
        as the matching down so the receiving app sees a consistent
        single/double/triple-click event pair."""
        self._left_down = False
        _post(CG.kCGEventLeftMouseUp, (self._cx, self._cy), CG.kCGMouseButtonLeft, self._click_count)

    def mouse_down_right(self) -> None:
        """Press the right mouse button. Re-syncs cursor first."""
        self._sync_pos()
        self._right_down = True
        _post(CG.kCGEventRightMouseDown, (self._cx, self._cy), CG.kCGMouseButtonRight, 1)

    def mouse_up_right(self) -> None:
        """Release the right mouse button."""
        self._right_down = False
        _post(CG.kCGEventRightMouseUp, (self._cx, self._cy), CG.kCGMouseButtonRight, 1)

    def mouse_down_middle(self) -> None:
        """Press the middle mouse button. Quartz uses
        ``kCGEventOtherMouseDown`` for buttons beyond left/right and
        the button index field selects which (Center == 2). Re-syncs
        cursor position first so the click lands where the user sees
        the cursor."""
        self._sync_pos()
        _post(CG.kCGEventOtherMouseDown, (self._cx, self._cy), CG.kCGMouseButtonCenter, 1)

    def mouse_up_middle(self) -> None:
        """Release the middle mouse button."""
        _post(CG.kCGEventOtherMouseUp, (self._cx, self._cy), CG.kCGMouseButtonCenter, 1)

    # ── Back / forward navigation ─────────────────────────────────────────
    #
    # macOS quirk: button index 3 ("OtherMouse 3") is grabbed by the OS
    # for Mission Control / App Exposé before any app sees the click.
    # Hardware verification on this user's system: button 4 routes to
    # browser forward as expected, but button 3 triggers Mission
    # Control instead of browser back.
    #
    # Using mouse buttons here would require finding a button index
    # the OS doesn't grab AND that browsers happen to interpret as
    # navigation — fragile across macOS versions and per-user system
    # settings. Cleaner solution: synthesise the universal Mac
    # keyboard shortcut, which Safari / Chrome / Firefox / Finder all
    # respect (Cmd+Left = back, Cmd+Right = forward).
    #
    # We treat each mouse_down_* as a complete atomic keystroke (down
    # + up) and make mouse_up_* a no-op, since "back/forward" is a
    # discrete-press action — holding the JC2 button shouldn't
    # repeat-fire navigation.

    def mouse_down_back(self) -> None:
        """Synthesise Cmd+Left — universal Mac back-navigation shortcut.

        Works in Safari, Chrome, Firefox, Edge, Finder, and any app
        that respects the system's standard back binding. Doesn't
        compete with Mission Control like mouse-button 3 does."""
        _post_cmd_key(0x7B)  # virtual keycode for Left Arrow

    def mouse_up_back(self) -> None:
        """No-op — back-keystroke fires atomically on press."""

    def mouse_down_forward(self) -> None:
        """Synthesise Cmd+Right — universal Mac forward-navigation shortcut."""
        _post_cmd_key(0x7C)  # virtual keycode for Right Arrow

    def mouse_up_forward(self) -> None:
        """No-op — forward-keystroke fires atomically on press."""

    def mouse_double_click(self) -> None:
        """Emit an explicit double-click sequence (down/up/down/up) with
        the click-count field set to 2 — for callers that want to
        force a double-click independent of the timing-based counter."""
        self._sync_pos()
        pos = (self._cx, self._cy)
        for event_type in (CG.kCGEventLeftMouseDown, CG.kCGEventLeftMouseUp,
                           CG.kCGEventLeftMouseDown, CG.kCGEventLeftMouseUp):
            ev = CG.CGEventCreateMouseEvent(None, event_type, pos, CG.kCGMouseButtonLeft)
            CG.CGEventSetIntegerValueField(ev, CG.kCGMouseEventClickState, 2)
            CG.CGEventPost(CG.kCGHIDEventTap, ev)

    def mouse_scroll(self, dx: int, dy: int) -> None:
        """Emit a 2-axis pixel-unit scroll wheel event. dx is horizontal,
        dy is vertical (positive = up, matching macOS convention)."""
        ev = CG.CGEventCreateScrollWheelEvent(None, CG.kCGScrollEventUnitPixel, 2, dy, dx)
        CG.CGEventPost(CG.kCGHIDEventTap, ev)
