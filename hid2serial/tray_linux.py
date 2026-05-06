"""
Linux tray app for hid2serial — Wayland + X11 compatible.

Uses StatusNotifierItem via Ayatana AppIndicator (D-Bus). Works on:
  Ubuntu 22.04+ Wayland (GNOME with AppIndicator extension preinstalled)
  KDE Plasma 5.x / 6.x (native KSNI)
  Cinnamon / XFCE / MATE / Budgie

Functionality:
  ▼ Click tray icon → menu:
    Status:     ● Running / ○ Stopped       (read systemctl is-active)
    Last scan:  <text>                      (read GET /readers/.../last на ErpNet.FP когато е достъпно)
    [Toggle Redirect]                       (start/stop service)
    Open config (/etc/hid2serial/config.yaml in default editor)
    View logs (journalctl -u hid2serial.service --follow)
    Quit tray (icon disappears, service unaffected)

Toggle uses `pkexec systemctl ...` — graphical password prompt unless a
polkit rule grants the `hid2serial` group passwordless start/stop. The
package ships such a rule under /etc/polkit-1/rules.d/.

When service is **stopped**, scanner works as plain HID keyboard (Odoo
POS scans into focused input field as usual). When **started**, daemon
grabs the device and emits to the configured pty. Toggling lets a
single laptop switch between "POS mode" and "redirect mode" without
unpairing the BLE link.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import urllib.error
import urllib.request

import gi  # type: ignore[import-not-found]

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import GLib, Gtk  # type: ignore[import-not-found]
from gi.repository import AyatanaAppIndicator3 as AppIndicator  # type: ignore[import-not-found]

from . import __version__

SERVICE_UNIT = "hid2serial.service"
ICON_RUNNING = "input-keyboard-symbolic"  # symbolic theme icons; available on every modern desktop
ICON_STOPPED = "input-mouse-symbolic"
APP_ID = "hid2serial-tray"
REFRESH_INTERVAL_S = 2.0

# Where the tray sends "Reconnect proxy reader" pokes. Read from
# environment so packagers / advanced users can override without
# editing the source. The reader id defaults to 'scanner1' which
# matches the value our installer / generated config uses, but is
# also overridable.
PROXY_RESET_URL = os.environ.get(
    "HID2SERIAL_PROXY_RESET_URL",
    "http://127.0.0.1:8001/readers/scanner1/reset",
)
PROXY_RESET_TIMEOUT_S = 3.0


def _systemctl(*args: str) -> tuple[int, str, str]:
    """Run `systemctl <args>` non-elevated. Returns (rc, stdout, stderr)."""
    try:
        p = subprocess.run(
            ["systemctl", *args],
            capture_output=True, text=True, timeout=5,
        )
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return 99, "", str(exc)


def _service_active() -> bool:
    rc, _, _ = _systemctl("is-active", "--quiet", SERVICE_UNIT)
    return rc == 0


def _toggle_service(activate: bool) -> None:
    """Start or stop the service via pkexec — pops a graphical
    password prompt unless a polkit rule grants passwordless toggle."""
    cmd = "start" if activate else "stop"
    subprocess.Popen(
        ["pkexec", "systemctl", cmd, SERVICE_UNIT],
        start_new_session=True,
    )


def _open_text(path: str) -> None:
    """Open a file in the user's default text editor."""
    subprocess.Popen(["xdg-open", path], start_new_session=True)


def _open_logs() -> None:
    """Spawn a terminal tailing the service journal."""
    # Prefer gnome-terminal / konsole / xterm in that order
    for term in ("gnome-terminal", "konsole", "xterm"):
        try:
            subprocess.Popen(
                [term, "-e", "journalctl", "-u", SERVICE_UNIT, "-f"],
                start_new_session=True,
            )
            return
        except FileNotFoundError:
            continue


def _silence_appindicator_init_warning():
    """libayatana-appindicator 0.5.93+ prints a deprecation notice on
    init even when there's no real fix to apply for GTK3 callers (the
    `-glib` variant is GTK4-only). Silence stderr just for the few ms
    of `Indicator.new()` so the user doesn't see noise on launch."""
    import os as _os
    saved = _os.dup(2)
    devnull = _os.open(_os.devnull, _os.O_WRONLY)
    _os.dup2(devnull, 2)

    def restore():
        _os.dup2(saved, 2)
        _os.close(saved)
        _os.close(devnull)

    return restore


class Tray:
    def __init__(self) -> None:
        _restore_stderr = _silence_appindicator_init_warning()
        try:
            self.indicator = AppIndicator.Indicator.new(
                APP_ID, ICON_STOPPED,
                AppIndicator.IndicatorCategory.HARDWARE,
            )
        finally:
            _restore_stderr()
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.indicator.set_title("hid2serial")

        self.menu = Gtk.Menu()
        self.status_item = Gtk.MenuItem(label="Status: …")
        self.status_item.set_sensitive(False)
        self.menu.append(self.status_item)

        self.toggle_item = Gtk.MenuItem(label="Toggle redirect")
        self.toggle_item.connect("activate", self._on_toggle)
        self.menu.append(self.toggle_item)

        # Fallback button — POSTs to the proxy's /readers/<id>/reset
        # so it drops its current fd and re-attaches to the (newly
        # created) pty. Used when the proxy is stuck holding a dead
        # fd from before a daemon restart and the auto-reconnect
        # heuristic hasn't yet triggered.
        self.poke_item = Gtk.MenuItem(label="Reconnect proxy reader")
        self.poke_item.connect("activate", self._on_poke_proxy)
        self.menu.append(self.poke_item)

        self.menu.append(Gtk.SeparatorMenuItem())

        item_cfg = Gtk.MenuItem(label="Open config…")
        item_cfg.connect(
            "activate",
            lambda _: _open_text("/etc/hid2serial/config.yaml"),
        )
        self.menu.append(item_cfg)

        item_logs = Gtk.MenuItem(label="View logs…")
        item_logs.connect("activate", lambda _: _open_logs())
        self.menu.append(item_logs)

        self.menu.append(Gtk.SeparatorMenuItem())

        item_about = Gtk.MenuItem(label=f"About hid2serial {__version__}")
        item_about.connect("activate", self._on_about)
        self.menu.append(item_about)

        item_quit = Gtk.MenuItem(label="Quit tray")
        item_quit.connect("activate", lambda _: Gtk.main_quit())
        self.menu.append(item_quit)

        self.menu.show_all()
        self.indicator.set_menu(self.menu)

        # Initial refresh + periodic
        self._refresh()
        GLib.timeout_add_seconds(int(REFRESH_INTERVAL_S), self._refresh)

    def _refresh(self) -> bool:
        running = _service_active()
        if running:
            self.indicator.set_icon_full(ICON_RUNNING, "redirect active")
            self.status_item.set_label("Status: ● Running (scanner grabbed)")
            self.toggle_item.set_label("Stop redirect")
        else:
            self.indicator.set_icon_full(ICON_STOPPED, "passthrough")
            self.status_item.set_label(
                "Status: ○ Stopped (scanner = plain HID keyboard)"
            )
            self.toggle_item.set_label("Start redirect")
        return True  # keep timer running

    def _on_toggle(self, _) -> None:
        running = _service_active()
        threading.Thread(
            target=_toggle_service,
            args=(not running,),
            daemon=True,
        ).start()
        # Schedule a refresh in 1.5s so the icon catches up after pkexec finishes
        GLib.timeout_add_seconds(2, self._refresh)

    def _on_poke_proxy(self, _) -> None:
        """Send a reset POST to the proxy in a background thread —
        the GUI never blocks on the network call. Notify the user
        with a transient menu-label tweak afterwards."""
        def _runner() -> None:
            try:
                req = urllib.request.Request(
                    PROXY_RESET_URL,
                    method="POST",
                    headers={"Content-Type": "application/json"},
                    data=b"{}",
                )
                with urllib.request.urlopen(
                    req, timeout=PROXY_RESET_TIMEOUT_S,
                ) as resp:
                    ok = 200 <= resp.status < 300
                    msg = "Reconnect proxy reader (✓)" if ok else \
                          f"Reconnect proxy reader (HTTP {resp.status})"
            except urllib.error.URLError as exc:
                msg = f"Reconnect proxy reader (unreachable: {exc.reason})"
            except Exception as exc:  # noqa: BLE001
                msg = f"Reconnect proxy reader (err: {exc})"

            def _show() -> bool:
                self.poke_item.set_label(msg)
                # Restore the original label after 3 s so the menu stays clean.
                GLib.timeout_add_seconds(
                    3,
                    lambda: (self.poke_item.set_label("Reconnect proxy reader"), False)[1],
                )
                return False

            GLib.idle_add(_show)

        threading.Thread(target=_runner, daemon=True).start()

    def _on_about(self, _) -> None:
        dlg = Gtk.AboutDialog()
        dlg.set_program_name("hid2serial")
        dlg.set_version(__version__)
        dlg.set_comments(
            "Daemon that turns a USB HID barcode scanner into a virtual "
            "serial port. Runs on the host; consumers (Odoo POS, fiscal "
            "printer drivers, ErpNet.FP) read the pty as a plain serial "
            "device."
        )
        dlg.set_website("https://github.com/rosenvladimirov/hid2serial")
        dlg.set_website_label("github.com/rosenvladimirov/hid2serial")
        dlg.set_license_type(Gtk.License.LGPL_3_0)
        dlg.run()
        dlg.destroy()


def main() -> int:
    # Allow Ctrl+C to terminate cleanly
    signal.signal(signal.SIGINT, lambda *_: Gtk.main_quit())
    Tray()
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
