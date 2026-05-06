"""
hid2serial CLI — typer entry point.

Commands:
    run                 Run the daemon against a config file
    list-readers        Enumerate /dev/input/event* keyboard devices
    doctor              Report perms, group, evdev availability
    test --reader NAME  Capture one scan + print to stdout (no symlink)
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Optional

import typer
import yaml

from . import __version__
from .core import AppConfig

app = typer.Typer(
    name="hid2serial",
    help="HID barcode reader → virtual serial port daemon",
    no_args_is_help=True,
    add_completion=False,
)


def _load_config(path: Path) -> AppConfig:
    if not path.exists():
        typer.echo(f"error: config file not found: {path}", err=True)
        raise typer.Exit(2)
    with path.open() as f:
        raw = yaml.safe_load(f) or {}
    return AppConfig.model_validate(raw)


def _setup_logging(level_name: str, log_file: Optional[str]) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file:
        handlers.append(logging.FileHandler(log_file))
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


@app.command()
def run(
    config: Path = typer.Option(
        Path("/etc/hid2serial/config.yaml"),
        "-c", "--config",
        help="Path to config.yaml",
    ),
):
    """Run the daemon — one runner thread per configured reader.

    The process stays in the foreground and exits cleanly on SIGTERM /
    SIGINT (suitable for systemd Type=simple)."""
    cfg = _load_config(config)
    _setup_logging(cfg.global_.log_level, cfg.global_.log_file)
    log = logging.getLogger("hid2serial")
    log.info("hid2serial %s starting (%d reader(s))", __version__, len(cfg.readers))

    try:
        from .linux import ReaderRunner  # noqa: WPS433
    except ImportError as exc:
        typer.echo(f"error: linux backend unavailable: {exc}", err=True)
        raise typer.Exit(3)

    runners: list = []
    for reader_cfg in cfg.readers:
        try:
            r = ReaderRunner(reader_cfg)
            r.start()
            runners.append(r)
        except Exception as exc:
            log.error("reader %r failed to start: %s", reader_cfg.name, exc)

    if not runners:
        log.error("No readers active — exiting")
        raise typer.Exit(4)

    stop = signal_event()
    while not stop.is_set():
        time.sleep(0.5)

    log.info("Shutdown signalled — stopping %d reader(s)", len(runners))
    for r in runners:
        try:
            r.stop()
        except Exception:
            log.exception("error stopping reader")
    log.info("hid2serial exited cleanly")


@app.command("list-readers")
def list_readers():
    """List /dev/input/event* devices that look like keyboards."""
    try:
        from .linux import list_keyboard_devices
    except ImportError as exc:
        typer.echo(f"error: linux backend unavailable: {exc}", err=True)
        raise typer.Exit(3)
    devices = list_keyboard_devices()
    if not devices:
        typer.echo("(no keyboard-class input devices found)")
        return
    for d in devices:
        typer.echo(
            f"{d['path']:25s}  vid={d['vid']:#06x}  pid={d['pid']:#06x}  {d['name']}"
        )


@app.command()
def doctor():
    """Report environment readiness."""
    try:
        import evdev  # noqa: F401
        evdev_ok = True
    except ImportError:
        evdev_ok = False
    in_input_group = False
    try:
        import grp
        groups = [g.gr_name for g in grp.getgrall() if os.getuid() in g.gr_mem]
        in_input_group = "input" in groups or os.geteuid() == 0
    except Exception:
        pass
    typer.echo(json.dumps({
        "version": __version__,
        "platform": sys.platform,
        "evdev_installed": evdev_ok,
        "running_as_root": os.geteuid() == 0,
        "in_input_group": in_input_group,
        "udev_rule_present": Path("/etc/udev/rules.d/99-hid2serial.rules").exists(),
        "systemd_unit_present": Path(
            "/etc/systemd/system/hid2serial.service").exists()
            or Path("/usr/lib/systemd/system/hid2serial.service").exists(),
    }, indent=2))


@app.command()
def test(
    reader: str = typer.Option(..., "--reader", help="reader name from config"),
    config: Path = typer.Option(
        Path("/etc/hid2serial/config.yaml"), "-c", "--config",
    ),
):
    """Capture one barcode from the named reader and print to stdout.

    Useful for verifying scanner config without setting up systemd /
    a real consumer process."""
    cfg = _load_config(config)
    _setup_logging("INFO", None)
    target = next((r for r in cfg.readers if r.name == reader), None)
    if target is None:
        typer.echo(f"error: reader {reader!r} not in config", err=True)
        raise typer.Exit(2)

    try:
        from .linux import ReaderRunner  # noqa
    except ImportError as exc:
        typer.echo(f"error: linux backend unavailable: {exc}", err=True)
        raise typer.Exit(3)

    runner = ReaderRunner(target)
    runner.start()
    try:
        # Read until newline appears on the pty; runner writes to its
        # master_fd in the read loop, we sniff via os.read on the same fd.
        # For simplicity we read from the slave path the symlink points
        # at — same data lands there.
        slave_path = target.output.linux.symlink
        with open(slave_path, "rb") as f:
            line = f.readline()
        sys.stdout.write(line.decode("utf-8", errors="replace"))
        sys.stdout.flush()
    finally:
        runner.stop()


def signal_event():
    """Return a `threading.Event` that gets set on SIGINT / SIGTERM."""
    import threading
    evt = threading.Event()
    def _handler(signum, _frame):
        evt.set()
    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)
    return evt


if __name__ == "__main__":
    app()
