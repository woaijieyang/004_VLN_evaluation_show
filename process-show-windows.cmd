@echo off
setlocal
where python >nul 2>nul
if errorlevel 1 (
  echo Python is required. Install Python and add it to PATH.
  pause
  exit /b 1
)
python -B "%~dp0tools\entry.py" %*
if errorlevel 1 pause
