@echo off
setlocal
cd /d "%~dp0"

set "PYTHON=%~dp0.runtime\python.exe"
if not exist "%PYTHON%" set "PYTHON=%~dp0.runtime\venv\Scripts\python.exe"
if not exist "%PYTHON%" if exist "%~dp0.runtime\system-python.txt" (
  set /p PYTHON=<"%~dp0.runtime\system-python.txt"
)
if not exist "%PYTHON%" (
  echo Runtime not found. Starting setup first...
  call "%~dp0setup.bat"
  set "PYTHON=%~dp0.runtime\python.exe"
  if not exist "%PYTHON%" set "PYTHON=%~dp0.runtime\venv\Scripts\python.exe"
  if not exist "%PYTHON%" if exist "%~dp0.runtime\system-python.txt" set /p PYTHON=<"%~dp0.runtime\system-python.txt"
)
if not exist "%PYTHON%" (
  echo [ERROR] Runtime is still unavailable. Please run setup.bat manually.
  pause
  exit /b 1
)

echo Starting talking-portrait auto-editing workflow...
echo Open http://127.0.0.1:7860 in your browser.
echo.
"%PYTHON%" "%~dp0app.py" %*
set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" (
  echo.
  echo [ERROR] Server stopped with exit code %EXITCODE%.
  pause
)
exit /b %EXITCODE%
