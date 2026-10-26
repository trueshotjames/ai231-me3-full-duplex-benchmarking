# Windows 10/11. Run in PowerShell from the fd-benchmark folder:  powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1
# Uses Python 3.13 (onnxruntime and sounddevice have no 3.14 wheels yet). Install it with:  py install 3.13
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$py = $null
foreach ($v in "3.13", "3.12", "3.11", "3.10") {
    try { & py -$v -c "pass" 2>$null; if ($LASTEXITCODE -eq 0) { $py = $v; break } } catch {}
}
if (-not $py) { Write-Error "Python 3.10-3.13 not found. Run:  py install 3.13   (or install from python.org with 'Add to PATH')." }
Write-Host "Using Python $py"
& py -$py -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m pip install -r requirements-score.txt
Write-Host ""
Write-Host "Installed. Run the benchmark with:  .\.venv\Scripts\python -m fdbench   (see docs\GUIDE.md)"
Write-Host "Virtual cables are only needed for connection option [b]; see docs\SETUP.md."
