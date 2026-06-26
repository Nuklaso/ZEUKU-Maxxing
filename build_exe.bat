@echo off
REM ===========================================================================
REM build_exe.bat -- build a standalone Windows exe for the ZEUKU Keep-Awake GUI
REM
REM Produces a single --windowed (no console) onefile exe via PyInstaller.
REM keep_awake.py is bundled automatically because keep_awake_gui.py imports it.
REM Output: dist\ZEUKU-KeepAwake.exe
REM ===========================================================================

REM Run from the folder this .bat lives in, regardless of the caller's CWD.
cd /d "%~dp0"

REM --- Pick an interpreter: prefer the py launcher, fall back to python. ------
set "PY=py"
where py >nul 2>nul
if errorlevel 1 (
    set "PY=python"
    where python >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] Neither "py" nor "python" was found on PATH.
        echo         Install Python 3.x and try again.
        pause
        exit /b 1
    )
)

echo Using interpreter: %PY%

REM --- Sanity-check the interpreter actually RUNS (catches the Microsoft Store
REM     execution-alias stub, which satisfies "where" but does not execute). ---
%PY% -c "import sys" 1>nul 2>nul
if errorlevel 1 (
    echo.
    echo [ERROR] "%PY%" was found but does not run ^(Microsoft Store stub?^).
    echo         Install real Python from https://www.python.org/downloads/
    echo         and tick "Add python.exe to PATH", then re-run this.
    echo.
    pause
    exit /b 1
)

REM --- Make sure PyInstaller is importable before we try to build. -----------
%PY% -c "import PyInstaller" 1>nul 2>nul
if errorlevel 1 (
    echo.
    echo [ERROR] PyInstaller is not installed for this interpreter.
    echo         Install it with:
    echo.
    echo             %PY% -m pip install pyinstaller
    echo.
    pause
    exit /b 1
)

REM --- Build. ----------------------------------------------------------------
echo.
echo Building ZEUKU-KeepAwake.exe ...
echo.
%PY% -m PyInstaller --noconfirm --onefile --windowed --name ZEUKU-KeepAwake keep_awake_gui.py
if errorlevel 1 (
    echo.
    echo [ERROR] PyInstaller build failed. See the output above.
    pause
    exit /b 1
)

echo.
echo ===========================================================================
echo  Build complete.
echo  Your exe is here:  "%~dp0dist\ZEUKU-KeepAwake.exe"
echo ===========================================================================
echo.
pause
