---
name: Bug report
about: Report something that isn't working correctly
title: ''
labels: bug
assignees: ''

---

**OS and version**
macOS / Windows — and the exact version (e.g. macOS 15.4, Windows 11 24H2).

**Joyglide version**
Binary download (which release?) or source build (which commit / branch?).

**Joy-Con 2 firmware version (if known)**
Visible in the Switch 2 system settings. Leave blank if unavailable.

**Expected behavior**
What did you expect to happen?

**Actual behavior**
What actually happened?

**Reproduction steps**
1.
2.
3.

**Application log**
Joyglide logs to stderr and to the macOS unified log (via `applog.py`).
- To capture stderr: launch from a terminal with `python main.py` (source) or redirect the binary's output.
- On macOS you can also open Console.app and filter by process name "Joyglide".
Paste the relevant lines here (redact any Bluetooth addresses if you prefer).

**Additional context**
Anything else that might help (controller model, pairing method, other BT devices connected, etc.).
