@echo off
setlocal
cd /d "%~dp0"

where powershell.exe >nul 2>nul
if errorlevel 1 (
  echo [ERROR] PowerShell not found. Windows 10 or newer is required.
  pause
  exit /b 1
)

rem Windows PowerShell 5.1 reads BOM-less UTF-8 files as GBK.
rem Re-create setup.ps1 with a UTF-8 BOM so non-ASCII text survives.
rem The copy stays inside scripts\ so $PSCommandPath still resolves the project root.
set "PS1=%~dp0scripts\_setup_%RANDOM%.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$c=Get-Content -Raw -Encoding UTF8 '%~dp0scripts\setup.ps1'; [IO.File]::WriteAllText('%PS1%',$c,[Text.UTF8Encoding]::new($true))"
if not exist "%PS1%" (
  echo [ERROR] Failed to prepare setup.ps1.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*
set "EXITCODE=%ERRORLEVEL%"
del "%PS1%" >nul 2>nul

echo.
if "%EXITCODE%"=="0" (
  echo Setup finished. Double-click start.bat to run.
) else (
  echo [ERROR] Setup failed with exit code %EXITCODE%. Please send the log above to the developer.
)
pause
exit /b %EXITCODE%
