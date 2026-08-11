@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=python"
if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=.venv\Scripts\python.exe"

echo Starting TimeLog OCR with %PYTHON_EXE%...
echo If the app closes immediately, read the error below.
echo.

"%PYTHON_EXE%" -c "import PySide6" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] PySide6 is not installed in this Python environment.
    echo Run:
    echo   %PYTHON_EXE% -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

"%PYTHON_EXE%" main.py %*
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%EXIT_CODE%"=="0" echo TimeLog OCR exited with code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%
