# Getting Help

## Have a question?

1. **Check the docs first**:
   - [`README.md`](../README.md) — quickstart, features, compatibility table
   - [`docs/TROUBLESHOOTING.md`](../docs/TROUBLESHOOTING.md) — known issues with fixes
   - [`docs/WINDOWS.md`](../docs/WINDOWS.md) — Windows-specific build / settings
   - [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) — protocol-level deep dive
   - [`docs/RESEARCH.md`](../docs/RESEARCH.md) — the 33 Hz BLE wall and what we tried

2. **Search existing issues**:
   - [Open issues](https://github.com/opsthomaz/joyglide/issues)
   - [Closed issues](https://github.com/opsthomaz/joyglide/issues?q=is%3Aissue+is%3Aclosed)
     — your problem may already have a documented fix

3. **Open a new issue**:
   - **Bug**: use the [bug report template](https://github.com/opsthomaz/joyglide/issues/new?template=bug_report.md).
     Please include OS + version, Python version (or "binary release"),
     Joy-Con firmware if you know it, and what you tried.
   - **Feature request**: use the
     [feature request template](https://github.com/opsthomaz/joyglide/issues/new?template=feature_request.md).
     The [`docs/ROADMAP.md`](../docs/ROADMAP.md) may already cover what you want.

## Have a security issue?

**Don't open a public issue.** See [`SECURITY.md`](SECURITY.md) for the
private reporting workflow.

## Want to contribute?

[`CONTRIBUTING.md`](CONTRIBUTING.md) walks through dev setup, the test
+ lint suite, and what we look for in PRs.

## Response times

This is a hobby project maintained in spare time. Expect:

- **Bug reports**: triaged within ~7 days.
- **Feature requests**: read but not always answered immediately.
  May land on [`docs/ROADMAP.md`](../docs/ROADMAP.md) for someone (or you) to pick up later.
- **Pull requests**: reviewed within ~14 days. Critical bug fixes
  faster.
- **Security reports**: see `SECURITY.md` for the formal SLA (7d
  acknowledge, 14d initial assessment).

If something is urgent and time-sensitive, mention it in the issue
title (`[urgent]` prefix is fine).
