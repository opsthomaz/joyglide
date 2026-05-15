# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared pytest fixtures and path setup.

Tests live in ``tests/`` but the production code is at the project root
(flat layout — see README "Project layout"). This conftest adds the
project root to ``sys.path`` so ``from utils import ...`` etc. resolve.

Also handles the mutmut case: when mutmut runs the suite, conftest's
parent is ``mutants/`` (where the mutated source copies live), and the
ORIGINAL project root is one level up. We add both paths in the right
order so mutated modules take precedence but unmutated modules still
resolve from the original tree.
"""
import sys
from pathlib import Path

# Where this conftest lives (parent of file = tests/, parent.parent = either
# the project root OR the mutants/ subdir during a mutmut run).
TESTS_PARENT = Path(__file__).resolve().parent.parent

if TESTS_PARENT.name == "mutants":
    # Mutmut mode: tests/ was copied into mutants/, originals are one up.
    MUTATED_ROOT  = TESTS_PARENT
    ORIGINAL_ROOT = TESTS_PARENT.parent
    # Mutated copies first (override), then originals (everything else).
    for p in (MUTATED_ROOT, ORIGINAL_ROOT):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
else:
    # Normal pytest run.
    PROJECT_ROOT = TESTS_PARENT
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
