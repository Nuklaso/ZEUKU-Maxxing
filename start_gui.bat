@echo off
REM ===========================================================================
REM start_gui.bat -- open the ZEUKU Keep-Awake GUI WINDOW (no console).
REM
REM This is the graphical app (keep_awake_gui.py). For the plain command-line
REM version (a console with log text) use start.bat instead.
REM ===========================================================================
cd /d "%~dp0"

REM Resolve a WINDOWLESS interpreter (pythonw / py -w) so no console appears.
set "PYW="
if exist ".\.venv\Scripts\pythonw.exe" set "PYW=.\.venv\Scripts\pythonw.exe"
if not defined PYW where pythonw >nul 2>nul && set "PYW=pythonw"

if defined PYW (
    start "" "%PYW%" "%~dp0keep_awake_gui.py"
    goto :eof
)

REM Fall back to the py launcher in windowed mode (-w uses pythonw).
where py >nul 2>nul && (
    start "" py -w "%~dp0keep_awake_gui.py"
    goto :eof
)

REM Last resort: plain python (a console window will also appear alongside).
where python >nul 2>nul && (
    start "" python "%~dp0keep_awake_gui.py"
    goto :eof
)

echo [ERROR] Kein Python gefunden. Bitte Python 3 installieren
echo         ^(https://www.python.org/downloads/, "Add to PATH" anhaken^)
echo         -- oder einfach dist\ZEUKU-KeepAwake.exe doppelklicken.
pause
