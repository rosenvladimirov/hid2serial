"""
Windows tray app — pystray-based, mirrors tray_linux.py functionality.

Toggle starts / stops the `hid2serial` Windows service via `sc.exe`.
When stopped, scanners work as plain HID keyboards (Windows native
behaviour); when running, the daemon grabs them and writes to the
configured com0com COM port.

Status: code-complete v0.2-dev — awaiting Windows hardware test.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

try:
    import pystray  # type: ignore[import-not-found]
    from PIL import Image, ImageDraw  # type: ignore[import-not-found]
except ImportError:
    pystray = None
    Image = None
    ImageDraw = None

from . import __version__

SERVICE_NAME = "hid2serial"
REFRESH_INTERVAL_S = 3.0


def _service_active() -> bool:
    """Run `sc query hid2serial` and return True if RUNNING."""
    try:
        out = subprocess.run(
            ["sc", "query", SERVICE_NAME],
            capture_output=True, text=True, timeout=5,
        )
        return "RUNNING" in (out.stdout or "")
    except Exception:
        return False


def _toggle_service(activate: bool) -> None:
    """Start or stop the Windows service. Requires admin or that the
    user has been granted `SeServiceLogonRight` on the service."""
    cmd = "start" if activate else "stop"
    # `net` UAC-prompts on most setups; `sc` is friendlier for
    # non-admin if SDDL on the service ACL allows.
    subprocess.Popen(
        ["sc", cmd, SERVICE_NAME],
        creationflags=0x08000000,  # CREATE_NO_WINDOW
    )


def _open_config() -> None:
    cfg = Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) / "hid2serial" / "config.yaml"
    if cfg.exists():
        os.startfile(str(cfg))  # noqa: S606
    else:
        webbrowser.open(f"file:///{cfg.parent}")


def _open_logs() -> None:
    """Open the Windows Event Viewer filtered to our service."""
    subprocess.Popen([
        "powershell", "-NoProfile", "-Command",
        "Get-EventLog -LogName Application -Source hid2serial -Newest 50 | "
        "Format-List | Out-String -Width 4096",
    ], creationflags=0)


def _icon_image(running: bool):
    """Generate a simple coloured square icon — pystray takes a PIL
    Image. Green for running, gray for stopped."""
    if Image is None or ImageDraw is None:
        return None
    color = (16, 185, 129) if running else (100, 116, 139)
    img = Image.new("RGB", (64, 64), color)
    draw = ImageDraw.Draw(img)
    # White HID-keyboard glyph approximation
    draw.rectangle((10, 22, 54, 50), fill=(255, 255, 255))
    for col in range(13, 53, 6):
        draw.rectangle((col, 28, col + 4, 32), fill=color)
        draw.rectangle((col, 38, col + 4, 42), fill=color)
    return img


def main() -> int:
    if pystray is None or Image is None:
        print(
            "Tray on Windows requires `pystray` and `Pillow`. Install:\n"
            "    pip install pystray pillow\n",
            file=sys.stderr,
        )
        return 2

    state = {"running": _service_active()}

    def make_menu():
        running = state["running"]
        return pystray.Menu(
            pystray.MenuItem(
                f"Status: {'● Running (scanner grabbed)' if running else '○ Stopped (HID keyboard)'}",
                None, enabled=False,
            ),
            pystray.MenuItem(
                "Stop redirect" if running else "Start redirect",
                lambda icon, item: threading.Thread(
                    target=_toggle_service,
                    args=(not running,), daemon=True,
                ).start(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open config…", lambda i, _: _open_config()),
            pystray.MenuItem("View logs…", lambda i, _: _open_logs()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                f"About hid2serial {__version__}",
                lambda i, _: webbrowser.open(
                    "https://github.com/rosenvladimirov/hid2serial",
                ),
            ),
            pystray.MenuItem("Quit tray", lambda i, _: i.stop()),
        )

    icon = pystray.Icon(
        "hid2serial",
        _icon_image(state["running"]),
        f"hid2serial {__version__}",
        make_menu(),
    )

    def refresh_loop():
        import time
        while True:
            time.sleep(REFRESH_INTERVAL_S)
            new_state = _service_active()
            if new_state != state["running"]:
                state["running"] = new_state
                icon.icon = _icon_image(new_state)
                icon.menu = make_menu()
                icon.update_menu()

    threading.Thread(target=refresh_loop, daemon=True).start()
    icon.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
