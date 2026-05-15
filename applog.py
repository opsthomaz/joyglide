# SPDX-License-Identifier: GPL-3.0-or-later
"""Centralized logging configuration for Joyglide.

Why a custom module:
    - Standard `logging.basicConfig` writes to stderr, which works in dev but
      gets swallowed by PyInstaller's `console=False` builds (no terminal
      attached). We send to stderr regardless — when running from terminal
      it shows; when running from a `.app` bundle it goes to the unified
      log (visible via `Console.app` or `log show --predicate ...`).
    - We keep emoji prefixes that the original prints used, but route them
      through the logger so they can be filtered, redirected, or silenced
      with a single env var.

Usage in any module:

    from applog import get_logger
    log = get_logger(__name__)
    log.info("⚡ HIGH_PRIORITY_CLASS active.")

Set ``JOYGLIDE_LOGLEVEL=DEBUG`` (or WARNING / ERROR) before launch to
override the level. Default is INFO.
"""
import logging
import os
import sys


# Single root configuration, applied once on first get_logger() call.
_configured = False


def _configure() -> None:
    """Idempotently install the root handler for the ``joyglide``
    logger namespace. Reads JOYGLIDE_LOGLEVEL from the environment
    if set; defaults to INFO. Subsequent calls are no-ops."""
    global _configured
    if _configured:
        return

    level_name = os.environ.get("JOYGLIDE_LOGLEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(
        # Keep messages compact; the emoji prefix in each log call already
        # signals severity to humans, and the level name adds clutter.
        "%(message)s"
    ))

    root = logging.getLogger("joyglide")
    root.setLevel(level)
    # Guard against duplicate handlers if _configure runs twice (it shouldn't,
    # but `_configured` could lose value across freeze/reimport edge cases).
    if not root.handlers:
        root.addHandler(handler)
    # Don't propagate to the python root logger — we own this namespace
    # and don't want libraries' loggers to inherit our format.
    root.propagate = False

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a child logger of the joyglide hierarchy.

    The first call configures the root handler; subsequent calls just
    return the named child logger. Pass ``__name__`` for module-scoped
    log lines.
    """
    _configure()
    # All loggers are children of "joyglide" so a single setLevel on
    # the root affects everything.
    if not name.startswith("joyglide"):
        name = f"joyglide.{name}"
    return logging.getLogger(name)
