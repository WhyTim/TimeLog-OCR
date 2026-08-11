$ErrorActionPreference = "Stop"
python -m PyInstaller --noconfirm --clean TimeLogOCR.spec
Write-Host "Build complete: dist\TimeLogOCR.exe"
