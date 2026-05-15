<!--
Thanks for the PR! A few quick checks below — feel free to delete
sections that don't apply (e.g., the test-plan section for a docs-only
change). The goal is signal, not bureaucracy.
-->

## Summary

<!-- One paragraph: what does this PR do, and why? -->

## Type of change

<!-- Pick all that apply -->

- [ ] Bug fix (`fix:`)
- [ ] New feature (`feat:`)
- [ ] Refactor — no user-visible change (`refactor:`)
- [ ] Documentation (`docs:`)
- [ ] Tests (`test:`)
- [ ] CI / build / tooling (`ci:` / `chore:`)
- [ ] Security fix (`security:`) — please also follow `SECURITY.md`

## Test plan

<!--
What did you do to verify the change? Examples:
- [ ] `pytest -q` passes (X tests)
- [ ] Tested on macOS X.Y with Joy-Con 2 (right side)
- [ ] Tested on Windows 11 with bleak version Z
- [ ] Smoke-launched the binary build (`./packaging/build.sh`)
- [ ] N/A (docs/comment only)
-->

## Checklist

- [ ] My code follows the project's architectural boundaries
      (`import-linter` runs in CI — verifies `parser/` ↛ `ui/`,
      `engine/` ↛ `ble/`, `osio/` is a leaf, etc.)
- [ ] I added/updated tests where applicable
- [ ] I updated `CHANGELOG.md` under `[Unreleased]`
- [ ] I updated relevant docs (README / ARCHITECTURE / WINDOWS /
      TROUBLESHOOTING) where applicable
- [ ] New `.py` files have the SPDX header
      (`# SPDX-License-Identifier: GPL-3.0-or-later`)
- [ ] No new runtime dependencies (or if there are, they're
      justified in the PR description)
- [ ] I ran the full lint suite locally and it passes
      (`ruff check . && mypy . && pyright && lint-imports && pytest -q`)

## Cross-platform note

<!--
If you only tested one OS, mention it. We accept platform-specific
PRs; we just want to know what's been verified.
-->

## Linked issues

<!-- "Fixes #123", "Refs #456" -->

---

By submitting this PR, I agree that my contribution is licensed under
**GPL-3.0-or-later** (the project's license; see `LICENSE`).
