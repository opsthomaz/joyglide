# Contributing to Joyglide

PRs welcome — and **especially welcome if you want to extend
compatibility to platforms or hardware we don't currently support**.
The codebase is small (~3000 lines of Python), well-commented, and
laid out so platform backends and parsers slot in cleanly via the
`osio/` and `parser/` packages — adding a new OS or controller variant
is a localised change, not a rewrite.

## High-impact contribution ideas

The [`docs/ROADMAP.md`](../docs/ROADMAP.md) has the full list with difficulty/value ratings.
Quick highlights:

- **Linux port** (`osio/mouse/linux.py` + `osio/hotkey/linux.py` +
  Linux branch in `osio/boost.py`) — Linux + BlueZ unlocks **200 Hz
  native**, matching the Switch console.
- **Pro Controller 2 / NSO GameCube support** — protocol already
  documented in [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md); mostly a parser extension.
- **macOS universal2 build** — single `.app` for arm64 + x86_64
  without Rosetta.
- **IMU-based motion prediction** — could halve perceived latency on
  macOS where the 33 Hz BLE rate is the floor.

## How to set up a dev environment

```bash
git clone git@github.com:your-username/joyglide.git
cd joyglide
git checkout -b feature/your-thing

# Python 3.13 or 3.14. We test both; CI uses 3.14.
python3.14 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt -r requirements-dev.txt
python main.py   # iterate
```

For native build validation:

```bash
./packaging/build.sh             # macOS .app
.\packaging\build_windows.ps1    # Windows .exe
```

## Running the test + lint suite

```bash
# Tests (unit + property + delegate-via-JoyCon)
pytest -q

# Mutation testing (single-file modules only — see packaging/run_mutmut.sh)
./packaging/run_mutmut.sh

# Static analysis stack — runs in CI, run locally to catch issues early
ruff check .
mypy .
pyright
bandit -c pyproject.toml -r . -ll
vulture ./*.py ble parser engine ui osio --min-confidence 80
deptry .
refurb ./*.py ble parser engine ui osio
codespell
yamllint .github/workflows/

# Architectural contract enforcement (HARD-FAIL in CI)
lint-imports

# Security
pip-audit --local --ignore-vuln PYSEC-2022-42969

# Complexity
xenon --max-absolute C --max-modules C --max-average A . \
  --ignore=research,build,dist,packaging,.venv,mutants,tests
```

## What we look for in PRs

- **No new dependencies** unless really needed (the project
  intentionally avoids heavy frameworks — Quartz/Win32 directly,
  no Qt, no Electron).
- **Architectural boundaries are enforced** — `import-linter` runs in
  CI with five contracts (e.g. `parser/` ↛ `ui/`, `engine/` ↛ `ble/`,
  `osio/` is a leaf). PRs that violate them fail the build. See
  `.importlinter`.
- **Cross-platform code stays cross-platform** — don't add
  `if sys.platform == "darwin"` inside `joycon.py`, `parser/`, or
  `engine/`. Add a function in `osio/` and have the dispatcher pick
  the right backend by `sys.platform`.
- **Pump and BLE rate logic must continue to auto-adapt** — the
  `_ble_period_ema` in `joycon.py` is what makes the same code feel
  right on macOS (33 Hz) and Windows (67 Hz). New platforms must hook
  into it the same way.
- **Comments explain the *why*** when it's not obvious from the code
  (especially around platform quirks like `convert_to returned null`,
  `MOUSEEVENTF_MOVE_NOCOALESCE`, `LSUIElement`, etc.). The repo is
  full of these — copy the style.
- **Update `CHANGELOG.md`** under the `[Unreleased]` section. Follow
  Keep a Changelog conventions (Added / Changed / Fixed / Removed /
  Deprecated / Security).
- **Don't break either platform** — local validation before pushing
  is highly encouraged. The CI catches build failures, but not
  runtime crashes.
- **License**: by submitting a PR, you agree your contribution is
  licensed under GPL-3.0-or-later (the project's license). Add an
  SPDX header to any new `.py` file:
  ```python
  # SPDX-License-Identifier: GPL-3.0-or-later
  ```

## Conventional commit message format

We don't enforce strict Conventional Commits, but PR titles benefit
from a clear prefix:

| Prefix       | When to use                                          |
|--------------|------------------------------------------------------|
| `feat:`      | New user-visible feature                             |
| `fix:`       | Bug fix                                              |
| `refactor:`  | Internal restructure, no user-visible change         |
| `docs:`      | Documentation only                                   |
| `test:`      | Tests only                                           |
| `ci:`        | CI/build workflow changes                            |
| `chore:`     | Dependency bumps, formatting, version-bump-only      |

## Got a Joy-Con but no time to code?

You can still help:

- **File an issue** with your hardware setup if the app behaves oddly
  (Mac model, macOS version, JC2 firmware version, BT chip on Windows,
  etc.).
- **Confirm compatibility** on hardware we haven't tested (any Mac
  older than M1, any Windows BT chip not in the README's compatibility
  table).
- **Test edge cases** — what happens with two Joy-Cons connected? When
  you put the laptop to sleep mid-session? When the JC2 battery dies
  during use?

Drop a note in [Issues](https://github.com/opsthomaz/joyglide/issues)
— even a "works on my MacBook Air 2017 with Rosetta" report is useful
data.

## Code of Conduct

Be kind. See [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) (in this same `.github/` directory).
