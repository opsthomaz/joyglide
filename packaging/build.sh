#!/bin/bash
# Joyglide — macOS local build (PyInstaller).
#
# Usage (from anywhere):
#   ./packaging/build.sh
#
# Output: dist/Joyglide.app
#
# Note: py2app was the original build tool, but it broke on Python 3.12+
# (upstream removed zlib.__file__ which py2app 0.28.10 still uses).
# PyInstaller handles both macOS and Windows, so we standardized on it.

set -e

# Run everything from the repo root regardless of where the script was
# invoked from — the spec, sips, and PyInstaller all use paths relative
# to that root.
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"

# 1. Generate the .icns from PNG (idempotent — sips skips if already exists)
mkdir -p Joyglide.iconset
./packaging/create_icons_script.sh

# 2. Clean old artifacts
rm -rf build dist

# 3. Build with PyInstaller
"$PYTHON" -m PyInstaller --clean --noconfirm packaging/joyglide_macos.spec

echo ""
if [ -d "dist/Joyglide.app" ]; then
    SIZE=$(du -sh dist/Joyglide.app | cut -f1)
    echo "✅ Build done: dist/Joyglide.app ($SIZE)"
    echo "   Open with:  open dist/Joyglide.app"
else
    echo "❌ Build failed — dist/Joyglide.app not found"
    exit 1
fi
