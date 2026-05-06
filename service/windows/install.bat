@echo off
REM ─────────────────────────────────────────────────────────────────
REM hid2serial — Windows install helper
REM
REM Run as Administrator.
REM
REM Steps performed:
REM   1. Install Python deps:    pywin32, pystray, pillow, evdev (=skip — Linux only)
REM   2. Install com0com COM pair (PortName=COM20 / COM21) if missing
REM   3. Register the Windows service `hid2serial` to run on boot
REM   4. Create %PROGRAMDATA%\hid2serial\config.yaml from example
REM   5. Add tray.exe to current user's Startup folder
REM
REM com0com download:
REM   https://sourceforge.net/projects/com0com/files/com0com/3.0.0.0/
REM Setup it once with admin; this script only validates / configures.
REM ─────────────────────────────────────────────────────────────────

setlocal enabledelayedexpansion

REM 1. Python deps
echo Installing Python dependencies...
pip install --upgrade hid2serial pywin32 pystray pillow pyserial pyyaml pydantic typer
if errorlevel 1 (
    echo ERROR: pip install failed.
    exit /b 1
)

REM Run pywin32 post-install (mandatory for the service to register)
python -m pywin32_postinstall -install

REM 2. Validate com0com presence (does NOT install — kernel driver
REM    install requires a manual + reboot step)
if not exist "%ProgramFiles%\com0com\setupc.exe" (
    echo WARNING: com0com is not installed in %ProgramFiles%\com0com.
    echo Download + install from https://sourceforge.net/projects/com0com/
    echo and create a port pair, e.g.:
    echo     "%%ProgramFiles%%\com0com\setupc.exe" install PortName=CNCA0,EmuBR=yes PortName=COM21,EmuBR=yes
    echo Then re-run this script.
    pause
)

REM 3. Register the service. Won't run yet — admin starts it via tray
REM    or `sc start hid2serial`.
python -m hid2serial.win_service --startup auto install
if errorlevel 1 (
    echo ERROR: service registration failed.
    exit /b 1
)

REM 4. Default config
set "CFG_DIR=%PROGRAMDATA%\hid2serial"
if not exist "%CFG_DIR%" mkdir "%CFG_DIR%"
if not exist "%CFG_DIR%\config.yaml" (
    REM Pulled from the package's installed config.example.yaml — pip
    REM installed it under the package directory; locate via Python.
    for /f "delims=" %%i in ('python -c "import hid2serial,os;print(os.path.join(os.path.dirname(hid2serial.__file__), '..', 'config.example.yaml'))"') do (
        copy /Y "%%i" "%CFG_DIR%\config.yaml" >nul 2>nul
    )
    if not exist "%CFG_DIR%\config.yaml" (
        echo WARNING: no example config found, manually create %CFG_DIR%\config.yaml
    )
)

REM 5. Tray autostart
set "STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "TRAY_BAT=%STARTUP_DIR%\hid2serial-tray.bat"
echo @echo off > "%TRAY_BAT%"
echo start "" "pythonw" -m hid2serial tray >> "%TRAY_BAT%"

echo.
echo ============================================================
echo  hid2serial Windows install complete.
echo ============================================================
echo.
echo  Next steps:
echo    - Edit %CFG_DIR%\config.yaml — set output.windows.com_pair
echo      to your com0com pair, e.g. ["CNCA0", "COM21"].
echo    - Start the service:  sc start hid2serial
echo    - Or use the tray:    pythonw -m hid2serial tray
echo.
endlocal
