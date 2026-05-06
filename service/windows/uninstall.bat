@echo off
REM Uninstall hid2serial from Windows. Run as Administrator.
REM Leaves com0com installed (it has its own uninstaller).

echo Stopping and removing hid2serial Windows service...
sc stop hid2serial 2>nul
python -m hid2serial.win_service remove 2>nul

echo Removing tray autostart...
del /q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\hid2serial-tray.bat" 2>nul

echo.
echo Config files NOT removed — manually delete %PROGRAMDATA%\hid2serial
echo if you want a clean slate.
echo.
echo Python package can be removed via:
echo     pip uninstall hid2serial
echo.
