"""
Windows Service wrapper for hid2serial.

Registers a Windows service called `hid2serial` that runs the daemon
in the background. Mirrors the Linux systemd unit's role.

Install:
    python -m hid2serial.win_service install --startup auto
Start:
    sc start hid2serial          (or via tray's Toggle button)
Remove:
    python -m hid2serial.win_service remove

The service reads its config from %ProgramData%\\hid2serial\\config.yaml
exactly like the Linux daemon reads /etc/hid2serial/config.yaml.
Format is the same.

Status: code-complete v0.2-dev — awaiting Windows hardware test.
Requires `pywin32` (`pip install pywin32`).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

try:
    import servicemanager  # type: ignore[import-not-found]
    import win32event  # type: ignore[import-not-found]
    import win32service  # type: ignore[import-not-found]
    import win32serviceutil  # type: ignore[import-not-found]
except ImportError:
    servicemanager = None
    win32event = None
    win32service = None
    win32serviceutil = None


CONFIG_PATH = Path(
    os.environ.get("PROGRAMDATA", "C:/ProgramData")
) / "hid2serial" / "config.yaml"


class Hid2SerialService(win32serviceutil.ServiceFramework if win32serviceutil else object):
    _svc_name_ = "hid2serial"
    _svc_display_name_ = "hid2serial — HID barcode reader to virtual COM"
    _svc_description_ = (
        "Daemon that grabs USB / Bluetooth HID barcode scanners and "
        "writes each scan to a com0com COM port, so consumers (Odoo "
        "POS, fiscal-printer drivers, ErpNet.FP) can read it as a "
        "regular serial device."
    )

    def __init__(self, args):
        if win32serviceutil is None:
            raise RuntimeError(
                "pywin32 is required (`pip install pywin32`) to run the "
                "Windows service wrapper."
            )
        super().__init__(args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self._runners: list = []

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.stop_event)
        for r in self._runners:
            try:
                r.stop()
            except Exception:
                pass

    def SvcDoRun(self):
        from .core import AppConfig
        from . import windows as win_backend
        import yaml

        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, ""),
        )

        if not CONFIG_PATH.exists():
            servicemanager.LogErrorMsg(
                f"Config not found: {CONFIG_PATH}. Aborting.",
            )
            return
        cfg = AppConfig.model_validate(yaml.safe_load(CONFIG_PATH.read_text()))
        for reader_cfg in cfg.readers:
            try:
                r = win_backend.ReaderRunner(reader_cfg)
                r.start()
                self._runners.append(r)
            except Exception as exc:
                servicemanager.LogErrorMsg(
                    f"Reader {reader_cfg.name!r} failed to start: {exc}"
                )

        if not self._runners:
            servicemanager.LogErrorMsg("No readers active — service exiting.")
            return

        # Block until SvcStop signals us
        win32event.WaitForSingleObject(self.stop_event, win32event.INFINITE)


def main():
    if win32serviceutil is None:
        print(
            "pywin32 is not installed. Run on Windows with:\n"
            "    pip install pywin32\n"
            "    python -m hid2serial.win_service install\n",
            file=sys.stderr,
        )
        sys.exit(2)
    win32serviceutil.HandleCommandLine(Hid2SerialService)


if __name__ == "__main__":
    main()
