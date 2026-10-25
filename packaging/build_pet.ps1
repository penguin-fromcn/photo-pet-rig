# Build the optional Windows desktop runtime as an onedir PyInstaller app.
#
# Usage (from the repository root):
#   powershell -ExecutionPolicy Bypass -File packaging\build_pet.ps1
#   powershell -ExecutionPolicy Bypass -File packaging\build_pet.ps1 -Python .venv\Scripts\python.exe
#
# The resulting folder contains PhotoDesktopPet.exe, _internal/assets
# (the synthetic demo) and a config.json that uses "%EXE_DIR%\assets".

param(
    [string]$Python = "python",
    [string]$Name = "PhotoDesktopPet",
    [string]$DemoAssets = "examples\demo_character"
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $root

if (-not (Test-Path $DemoAssets)) {
    throw "demo assets not found: $DemoAssets"
}

$dist = Join-Path $root "dist\pet"
$work = Join-Path $root "build\pet"
$spec = Join-Path $root "build\pet-spec"
$demoFull = (Resolve-Path $DemoAssets).Path
$configFull = (Resolve-Path "packaging\pet_config.json").Path
$entryFull = (Resolve-Path "packaging\pet_entry.py").Path

& $Python -m PyInstaller `
    --noconfirm --clean --windowed --onedir `
    --name $Name `
    --distpath $dist `
    --workpath $work `
    --specpath $spec `
    --add-data "$demoFull;assets" `
    --add-data "$configFull;." `
    $entryFull

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$appDir = Join-Path $dist $Name
Copy-Item -LiteralPath "packaging\pet_config.json" -Destination (Join-Path $appDir "config.json") -Force
Write-Host ""
Write-Host "Built: $(Join-Path $appDir ($Name + '.exe'))"
Write-Host "Smoke: $(Join-Path $appDir ($Name + '.exe')) --smoke-test 2 --no-tray --smoke-report smoke.json"
