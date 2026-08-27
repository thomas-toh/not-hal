@echo off
REM Gemma installer (Windows). Run:  install.bat        -- add "gpu" for NVIDIA CUDA speech-to-text.
setlocal
cd /d "%~dp0"

REM Long Paths OFF makes PySide6 half-install SILENTLY (import works, QtQuick missing).
REM Detect only -- turning it on is an HKLM change plus a reboot, so that stays the user's call.
set LONGPATHS=
for /f "tokens=3" %%A in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\FileSystem" /v LongPathsEnabled 2^>nul') do set LONGPATHS=%%A
if not "%LONGPATHS%"=="0x1" (
    echo.
    echo   Windows Long Paths is OFF. The overlay would install broken, so stopping here.
    echo   In an ADMIN PowerShell, then reboot:
    echo.
    echo     Set-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' LongPathsEnabled 1
    echo.
    exit /b 1
)

py -3 -m venv .venv || exit /b 1
call .venv\Scripts\activate.bat || exit /b 1
pip install -e . || exit /b 1
if /i "%~1"=="gpu" (
    pip install -e ".[gpu-cuda]" || exit /b 1
)

echo.
echo   Installed. To start Gemma:
echo.
echo     .venv\Scripts\activate
echo     py run.py
echo.
echo   The API key goes in the overlay's Settings, not a file.
echo.
