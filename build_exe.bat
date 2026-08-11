@echo off
setlocal
chcp 65001 >nul
python -m PyInstaller --noconfirm --clean TimeLogOCR.spec
if errorlevel 1 (
  echo Build failed.
  exit /b 1
)
echo Build complete: dist\TimeLogOCR.exe
