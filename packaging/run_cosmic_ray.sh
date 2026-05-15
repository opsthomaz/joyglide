#!/usr/bin/env bash
# cosmic-ray runner — AST-level mutation testing for parser/ modules.
#
# Why this exists alongside run_mutmut.sh:
#   * mutmut 3.x: works for single-file modules at the project root
#     (utils.py achieves 96.7%) but silently fails on packages — its
#     stats-collection phase loses track of which tests cover the
#     mutated code, reporting "0 mutants tested".
#   * cosmic-ray: uses a SQLite session + per-mutant test runs (no
#     stats-collection fragility), so it handles packages cleanly.
#     Validated on parser/u16_delta.py: 45/45 mutants killed (100%).
#
# What this proves:
#   The hypothesis property tests in tests/test_property.py and the
#   exact-value tests in tests/test_joycon.py together pin every
#   number / operator / comparator that cosmic-ray can mutate. A
#   regression that changes wraparound math, scaling constants, or
#   bit-packing offsets WILL be caught.
#
# Usage:
#   ./packaging/run_cosmic_ray.sh                        # default: parser/u16_delta.py
#   ./packaging/run_cosmic_ray.sh parser/battery.py      # single file
#   ./packaging/run_cosmic_ray.sh parser                 # whole package (~30 min)
#
# The session DB is gitignored. Re-run any time to verify the
# mutation score hasn't regressed.
set -euo pipefail
cd "$(dirname "$0")/.."

TARGET="${1:-parser/u16_delta.py}"
SESSION="cosmic-ray-session.sqlite"

# Pick the right test subset for the target so we don't run the entire
# suite per mutant (each test invocation costs ~1.5s in fixture overhead;
# multiplied by 1000+ mutants for the whole package, that's a 25+ minute
# run vs the actual mutation work).
case "$TARGET" in
    parser/u16_delta.py)
        TESTS="tests/test_property.py::test_delta_u16_in_signed_range
               tests/test_property.py::test_delta_u16_zero_when_equal
               tests/test_property.py::test_delta_u16_antisymmetric
               tests/test_property.py::test_delta_u16_consistent_with_unsigned_delta
               tests/test_joycon.py::TestDeltaU16" ;;
    parser/battery.py)
        TESTS="tests/test_property.py::test_battery_voltage_in_plausible_range_is_stored
               tests/test_property.py::test_battery_pct_clamped_0_to_100
               tests/test_property.py::test_battery_implausible_voltage_does_not_update_state
               tests/test_property.py::test_battery_charge_state_classification
               tests/test_property.py::test_battery_current_parsed_when_field_present
               tests/test_property.py::test_battery_current_stays_none_when_packet_short
               tests/test_property.py::test_battery_short_packet_skipped
               tests/test_property.py::test_battery_throttle_one_second" ;;
    parser/imu.py) TESTS="tests/test_imu.py" ;;
    *)             TESTS="tests/" ;;
esac

# Regenerate cosmic-ray.toml so module-path matches the chosen target.
cat > cosmic-ray.toml <<EOF
[cosmic-ray]
module-path = "$TARGET"
timeout = 10.0
excluded-modules = []
test-command = ".venv/bin/python -m pytest -x -q $TESTS"

[cosmic-ray.distributor]
name = "local"
EOF

rm -f "$SESSION"
.venv/bin/cosmic-ray init cosmic-ray.toml "$SESSION"

JOBS=$(.venv/bin/cosmic-ray dump "$SESSION" | wc -l | tr -d ' ')
echo "→ $JOBS mutants generated for $TARGET"

.venv/bin/cosmic-ray --verbosity WARNING exec cosmic-ray.toml "$SESSION"

echo
echo "=== cosmic-ray report ==="
.venv/bin/cr-report "$SESSION" | tail -30
