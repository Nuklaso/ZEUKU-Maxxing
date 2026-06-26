@echo off
REM ============================================================
REM  start.bat -- launch keep_awake.py (smart mode by default)
REM  Stop it any time with Ctrl+C in the window that opens.
REM
REM  Forwards extra args, e.g.:
REM     start.bat --force --interval 60
REM     start.bat --method both --idle-threshold 120
REM  Bare "start.bat" runs pure defaults (smart, 240s/30s, key,
REM  keep-display on).
REM ============================================================

setlocal
cd /d "%~dp0"

REM Resolve ONE interpreter in stages, then launch exactly once:
REM   1) project-local venv  .\.venv\Scripts\python.exe  (if present)
REM   2) the "py" launcher
REM   3) "python" on PATH
set "PYEXE="
if exist ".\.venv\Scripts\python.exe" set "PYEXE=.\.venv\Scripts\python.exe"
if not defined PYEXE where py     >nul 2>nul && set "PYEXE=py"
if not defined PYEXE where python >nul 2>nul && set "PYEXE=python"

if not defined PYEXE (
    echo.
    echo [ERROR] No Python interpreter found.
    echo   Install Python 3 from https://www.python.org/downloads/
    echo   and tick "Add python.exe to PATH" during setup, then re-run this.
    echo.
    pause
    endlocal
    exit /b 9009
)

"%PYEXE%" "%~dp0keep_awake.py" %*

echo.
echo Script exited with code %ERRORLEVEL%.
pause
endlocal
