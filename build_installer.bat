@echo off
setlocal
chcp 65001 >nul

call "%~dp0build_exe.bat"
if errorlevel 1 exit /b 1

set "ISCC_PATH=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC_PATH%" set "ISCC_PATH=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC_PATH%" set "ISCC_PATH=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC_PATH%" (
  echo Inno Setup 6 is required to build the installer.
  exit /b 1
)

"%ISCC_PATH%" "%~dp0installer\TimeLogOCR.iss"
if errorlevel 1 exit /b 1

echo Installer complete: dist\installer\TimeLogOCRSetup-1.6.0.exe
