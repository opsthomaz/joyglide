# SPDX-License-Identifier: GPL-3.0-or-later
"""Platform dispatcher for cursor injection.

Re-exports ``InputSimulator``, ``check_accessibility`` and
``request_accessibility`` from the appropriate platform backend. Importing
this module is safe on any OS — on macOS / Windows it loads the real
backend; on Linux (or any other unsupported platform) it loads a no-op
stub so that imports succeed during testing / CI / lint runs that don't
actually move a cursor.

Calling InputSimulator() on the stub will raise — so the runtime app
still won't pretend to work on Linux. Only inert imports succeed.
"""
import sys

if sys.platform == "darwin":
    from osio.mouse.macos import InputSimulator, check_accessibility, request_accessibility
elif sys.platform == "win32":
    from osio.mouse.windows import InputSimulator, check_accessibility

    def request_accessibility() -> None:
        """No-op on Windows — SendInput needs no per-app permission."""
elif sys.platform.startswith("linux"):
    # Both mypy and pyright on Ubuntu CI resolve evdev fully (Linux-only
    # dep), so they see linux.InputSimulator's true class signature and
    # flag it as incompatible with the stub `class InputSimulator` defined
    # below. The macos/windows branches escape this only because their
    # native deps (Quartz, winrt) aren't resolvable on Ubuntu. This is the
    # platform-dispatcher pattern, not a real type error.
    from osio.mouse.linux import InputSimulator, check_accessibility  # type: ignore[no-redef]  # pyright: ignore[reportAssignmentType]

    def request_accessibility() -> None:
        """No-op on Linux — uinput permission is granted via the udev
        rule + ``input`` group at OS-config time, not at runtime."""
else:
    # Stub backend — sufficient to satisfy `from osio.mouse import InputSimulator`
    # during test collection or static analysis on platforms without a real
    # backend. Instantiating the stub raises so the real app fails loudly
    # if launched here.
    class InputSimulator:  # type: ignore[no-redef]
        def __init__(self) -> None:
            raise RuntimeError(
                f"InputSimulator is not implemented on '{sys.platform}'. "
                f"Supported: macOS (darwin), Windows (win32), Linux."
            )

    def check_accessibility() -> bool:  # type: ignore[no-redef]
        return False

    def request_accessibility() -> None:  # type: ignore[no-redef]
        return
