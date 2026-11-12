@echo off
setlocal
cd /d "%~dp0"

where powershell.exe >nul 2>nul
if errorlevel 1 (
  echo [ERROR] PowerShell is required.
  pause
  exit /b 1
)

echo Starting Fish Speech first, then the talking-portrait studio...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-all.ps1" %*
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
  echo.
  echo [ERROR] Startup failed with exit code %EXITCODE%.
  pause
)
exit /b %EXITCODE%
