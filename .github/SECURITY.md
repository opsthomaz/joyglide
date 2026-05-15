# Security Policy

## Supported versions

Only the **latest minor release** receives security updates. Older
versions remain available on the [Releases page](https://github.com/opsthomaz/joyglide/releases)
but won't get backported fixes.

| Version  | Supported          |
|----------|--------------------|
| 0.1.x    | ✅ yes             |

## Reporting a vulnerability

**Do not open a public GitHub issue for security bugs.** Use one of
these private channels instead:

- **Preferred**: [GitHub Security Advisories](https://github.com/opsthomaz/joyglide/security/advisories/new)
  — encrypted, threaded, includes a built-in CVE-request workflow.
- **Alternative**: email `tdoronides@gmail.com` with the subject prefix
  `[joyglide security]`.

Please include:
- Affected version(s)
- A clear description of the issue
- Reproduction steps (or PoC)
- Your suggested severity rating, if any

## Response timeline

- **Acknowledgement**: within 7 days.
- **Initial assessment**: within 14 days.
- **Fix or mitigation**: depends on severity. Critical issues get
  patched and released within 30 days; lower-severity issues are
  rolled into the next regular release.

You'll be credited in the security advisory and the CHANGELOG unless
you ask to remain anonymous.

## Scope

### In scope
- The `joyglide` source code in this repository.
- The `Joyglide.app` (macOS) and `Joyglide.exe` (Windows)
  binaries published to GitHub Releases.
- The CI/build pipeline configurations in `.github/workflows/`.

### Out of scope
- Third-party dependencies (`bleak`, `pystray`, `customtkinter`, etc.)
  — report those upstream. Their CVEs are tracked via `pip-audit` in
  our CI and documented in `.pip-audit-ignore` when not actionable.
- Reference research clones in `research/` — these are clones of other
  projects for cross-reference only and are NOT shipped.
- Bluetooth-stack vulnerabilities (macOS CoreBluetooth, Windows WinRT
  BLE) — those are OS-level and need to go to Apple/Microsoft.

## Threat model

Joyglide is a **local desktop input app**. It:
- Runs on the user's machine with the user's permissions.
- Talks to a paired Bluetooth Low Energy device (Joy-Con 2).
- Synthesises mouse events via OS APIs (Quartz CGEvent / Win32 SendInput).
- Reads/writes its own settings JSON in the OS user-data directory.

It does **not**:
- Listen on any network port.
- Make outbound HTTP calls.
- Process untrusted user input from disk (settings file is not parsed
  with `eval`/`exec`).
- Run as root/admin.

The realistic attack surface is therefore narrow: malformed BLE input
reports from a hostile / spoofed Joy-Con (defended by length checks
and bounds in `parser/*`), or malformed `settings.json` (only
`json.load` is used; no code execution).

## Known security-adjacent items

- **CVE PYSEC-2022-42969** (`py` 1.11.0, transitive via `interrogate`):
  ReDoS via crafted Subversion repo info. **Not applicable** — the
  vulnerable code path (`py.path.svnwc`) is never reached by anything
  in this project. Documented and suppressed in `.pip-audit-ignore`.
