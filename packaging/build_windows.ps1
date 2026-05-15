# Joyglide — Windows build script
#
# Creates a Python 3.14 venv (or 3.13 — both supported), installs deps,
# generates the .ico from PNG, runs PyInstaller. Final output:
# dist\Joyglide.exe (single-file, no console window).
#
# Prerequisites:
#   - Python 3.13+ installed and on PATH (or via py launcher: `py -3.14`)
#
# Usage (from the repo root):
#   PowerShell in the project folder:
#     .\packaging\build_windows.ps1
#
# Common errors:
#   "convert_to returned null" → bleak/winrt mismatch — usually leftover
#                                winsdk install. Wipe the venv and re-run
#                                this script (it will recreate .venv-win).

$ErrorActionPreference = "Stop"

# Run from the repo root regardless of where the script was invoked from —
# the spec uses relative paths (assets\, main.py) anchored there.
Set-Location (Join-Path $PSScriptRoot "..")

Write-Host "Joyglide — Windows build" -ForegroundColor Cyan
Write-Host ""

# ── 1. Python 3.13+ detection (3.14 preferred) ──────────────────────────
$pythonCmd = $null
$candidates = @(
    @{ Cmd = "py";     Args = @("-3.14", "--version"); LauncherFlag = "-3.14" },
    @{ Cmd = "py";     Args = @("-3.13", "--version"); LauncherFlag = "-3.13" },
    @{ Cmd = "python"; Args = @("--version");          LauncherFlag = $null  }
)

foreach ($c in $candidates) {
    try {
        $ver = & $c.Cmd @($c.Args) 2>&1
        if ($ver -match "Python 3\.(1[34])") {
            if ($c.LauncherFlag) {
                $pythonCmd = @($c.Cmd, $c.LauncherFlag)
            } else {
                $pythonCmd = @($c.Cmd)
            }
            Write-Host "[OK] Found: $ver" -ForegroundColor Green
            break
        } elseif ($ver -match "Python 3\.(\d+)") {
            Write-Host "[skip] $($c.Cmd) is Python 3.$($matches[1]) — need 3.13+" -ForegroundColor DarkGray
        }
    } catch { }
}

if (-not $pythonCmd) {
    Write-Host ""
    Write-Host "Python 3.13+ not found." -ForegroundColor Red
    Write-Host "Install from https://www.python.org/downloads/"
    Write-Host "Tick 'Add to PATH' during installation."
    exit 1
}

# ── 2. venv ─────────────────────────────────────────────────────────────
if (-not (Test-Path ".venv-win")) {
    Write-Host ""
    Write-Host "Creating venv .venv-win..." -ForegroundColor Cyan
    & $pythonCmd[0] @($pythonCmd[1..($pythonCmd.Length - 1)] + @("-m", "venv", ".venv-win"))
}
$venvPython = Join-Path (Resolve-Path ".venv-win") "Scripts\python.exe"
Write-Host "[OK] venv at .venv-win" -ForegroundColor Green

# ── 3. Deps (read from requirements.txt — single source of truth) ───────
Write-Host ""
Write-Host "Installing deps from requirements.txt..." -ForegroundColor Cyan
& $venvPython -m pip install --upgrade pip --quiet
& $venvPython -m pip install -r requirements.txt --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Host "Dep install failed." -ForegroundColor Red
    exit 1
}
Write-Host "[OK] deps installed" -ForegroundColor Green

# ── 4. Generate .ico from the PNG ───────────────────────────────────────
$iconPath = "assets\joyglide.ico"
$pngPath  = "assets\joyglide.png"
if ((Test-Path $pngPath) -and (-not (Test-Path $iconPath))) {
    Write-Host ""
    Write-Host "Converting $pngPath -> $iconPath..." -ForegroundColor Cyan
    & $venvPython -c @"
from PIL import Image
img = Image.open('$pngPath').convert('RGBA')
sizes = [(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)]
img.save('$iconPath', sizes=sizes)
print('icon written')
"@
}

# ── 5. Wipe old build artifacts ─────────────────────────────────────────
Write-Host ""
Write-Host "Cleaning previous build..." -ForegroundColor Cyan
if (Test-Path "build") { Remove-Item -Recurse -Force build }
if (Test-Path "dist")  { Remove-Item -Recurse -Force dist }

# ── 6. PyInstaller ──────────────────────────────────────────────────────
Write-Host ""
Write-Host "Running PyInstaller..." -ForegroundColor Cyan
& $venvPython -m PyInstaller --clean --noconfirm packaging\joyglide_windows.spec
if ($LASTEXITCODE -ne 0) {
    Write-Host "PyInstaller failed." -ForegroundColor Red
    exit 1
}

# ── 7. Done ─────────────────────────────────────────────────────────────
$exePath = Resolve-Path "dist\Joyglide.exe" -ErrorAction SilentlyContinue
if ($exePath) {
    $size = (Get-Item $exePath).Length / 1MB
    Write-Host ""
    Write-Host "[OK] Build done: $exePath ($([math]::Round($size,1)) MB)" -ForegroundColor Green
    Write-Host ""
    Write-Host "To run: double-click the .exe, or:"
    Write-Host "  .\dist\Joyglide.exe"
} else {
    Write-Host "Build finished but dist\Joyglide.exe was not found." -ForegroundColor Red
    exit 1
}
