#!/usr/bin/env bash
# Mutmut runner — works around mutmut 3.x's limitations with packages.
#
# What works:
#   * Single-file modules at the project root (e.g. utils.py).
#     Achieves >95% mutation score with our test suite.
#
# What partially works:
#   * Module files inside packages (parser/u16_delta.py, etc.). The
#     pre-copy trick below makes them importable, but mutmut's stats-
#     collection phase still loses track of which tests cover the
#     mutated code (probably an interaction between the trampoline
#     pattern, our conftest's sys.path manipulation, and mutmut's
#     coverage instrumentation). Reports "0 mutants tested" silently.
#
# What to do next: cosmic-ray is an alternative mutation tool with
# better package support. Not adopted yet — adding another tool just
# for one corner of the codebase doesn't pay back. The pure logic
# we DO want fuzz-tested (delta_u16, decode_joystick, battery.parse)
# is covered via hypothesis property tests in tests/test_property.py
# instead, which generate ~100 inputs per property and don't have
# the package-import issue.
#
# Usage:
#   ./packaging/run_mutmut.sh              # mutate utils.py (single-file)
#   ./packaging/run_mutmut.sh utils.py     # explicit
set -euo pipefail

cd "$(dirname "$0")/.."

# Default — utils.py only (the single-file case that mutmut handles
# cleanly). Override on command line for experimentation.
TARGETS="${*:-utils.py}"

# Fresh slate.
rm -rf mutants .mutmut-cache setup.cfg

cat > setup.cfg << EOF
[mutmut]
paths_to_mutate=$TARGETS
backup=False
runner=python -m pytest -x -q
tests_dir=tests/
EOF

# Pre-mirror every first-party .py into mutants/ so the conftest's
# "mutants/ first on sys.path" trick can resolve every import. Mutmut
# will overwrite the targeted paths with mutated copies and leave the
# rest as originals.
for src in $(find ble parser engine ui osio -name '*.py' 2>/dev/null); do
    target="mutants/$src"
    mkdir -p "$(dirname "$target")"
    cp "$src" "$target"
done

# Real mutation run.
.venv/bin/mutmut run

echo
echo "=== Results breakdown ==="
.venv/bin/mutmut results | awk -F': ' '{print $2}' | sort | uniq -c | sort -rn
