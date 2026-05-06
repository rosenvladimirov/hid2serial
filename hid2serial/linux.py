"""
Linux backend — evdev `grab()` + `pty.openpty()` + symlink to a stable
path the consumer expects.

Each configured reader runs in its own daemon thread:

  1. Resolve the input device by VID/PID/name_regex/path
  2. Acquire exclusive grab so scans don't bleed into other UI focus
  3. Open a pty pair; symlink the slave to e.g. `/dev/ttyV0`
  4. Read evdev events forever, translate via core.translate(), buffer
     into LineBuffer, on terminator → flush + write to pty master fd
  5. On signal/stop → ungrab, close pty, remove symlink
"""

from __future__ import annotations

import logging
import os
import pty
import re
import threading
from typing import Optional

try:
    import evdev
except ImportError:
    evdev = None  # type: ignore[assignment]

from .core import (
    KEY_CAPSLOCK,
    KEY_ENTER,
    LineBuffer,
    ReaderConfig,
    SHIFT_KEYS,
    TERMINATOR_KEYS,
    translate,
)

_logger = logging.getLogger(__name__)


def list_keyboard_devices() -> list[dict]:
    """Return one dict per `/dev/input/event*` keyboard device."""
    if evdev is None:
        return []
    out: list[dict] = []
    for path in evdev.list_devices():
        try:
            d = evdev.InputDevice(path)
        except Exception:
            continue
        try:
            caps = d.capabilities().get(evdev.ecodes.EV_KEY, [])
            if KEY_ENTER not in caps:
                continue
            out.append({
                "path": path,
                "name": d.name,
                "vid": d.info.vendor,
                "pid": d.info.product,
                "bustype": d.info.bustype,
            })
        finally:
            d.close()
    return out


# Hints used to skip internal devices when `any_external: true`.
# Ordered by likelihood — speeds up the scan loop when the laptop
# kbd / mouse appear before the scanner in evdev's enumeration.
#
# Strategy is "exclude known-not-scanner" rather than "include known-
# scanner" because barcode-scanner names are wildly inconsistent (no-
# name BLE devices may report just their MAC, OEM USB scanners may
# report nothing at all). Bias is therefore towards skipping anything
# that looks like a regular keyboard/mouse/system button.
_INTERNAL_HINTS = (
    # Laptop / desktop integrated devices
    "translated", "trackpoint", "trackpad", "touchpad",
    "lid switch", "video bus", "fn key",
    "power button", "sleep button", "thinkpad",
    # Common security tokens
    "yubikey", "fido",
    # Mice (any vendor) — never barcode scanners
    "mouse",
    # Brand-name keyboards (Logitech / Apple / Microsoft) — these are
    # regular kbds, not barcode scanners. Note: this filters out
    # well-known *generic* keyboard product strings; no-name barcode
    # scanners typically don't carry these brand strings.
    "logitech wireless keyboard", "apple keyboard", "microsoft natural",
    "magic keyboard", "magic trackpad",
    # HID-class control surfaces present on every modern laptop
    "consumer control", "system control",
)


def _looks_internal(d: dict) -> bool:
    """Heuristic — is this an internal/peripheral device, not a barcode
    scanner? Used by `any_external` matching mode."""
    name = (d.get("name") or "").lower()
    return any(hint in name for hint in _INTERNAL_HINTS)


def resolve_device(cfg_match) -> Optional[str]:
    """Return the path of the first device matching the configured criteria."""
    if cfg_match.device_path:
        return cfg_match.device_path if os.path.exists(cfg_match.device_path) else None

    # If no specific criteria given OR any_external is True, fall back
    # to "first non-internal HID keyboard". Empty `match: {}` blocks
    # also land here — the most user-friendly default.
    no_specific = (
        cfg_match.vid is None
        and cfg_match.pid is None
        and not cfg_match.name_regex
    )
    if cfg_match.any_external or no_specific:
        for d in list_keyboard_devices():
            if not _looks_internal(d):
                return d["path"]
        return None

    name_re = re.compile(cfg_match.name_regex, re.I) if cfg_match.name_regex else None
    for d in list_keyboard_devices():
        if cfg_match.vid is not None and d["vid"] != cfg_match.vid:
            continue
        if cfg_match.pid is not None and d["pid"] != cfg_match.pid:
            continue
        if name_re and not name_re.search(d["name"] or ""):
            continue
        return d["path"]
    return None


class ReaderRunner:
    """One ReaderConfig → one running thread + pty + symlink lifecycle."""

    def __init__(self, cfg: ReaderConfig) -> None:
        if evdev is None:
            raise RuntimeError(
                "python-evdev is not installed (`pip install evdev`)"
            )
        if cfg.output.linux is None:
            raise ValueError(
                f"Reader {cfg.name!r} has no linux.output configured"
            )
        self.cfg = cfg
        self._dev: Optional["evdev.InputDevice"] = None
        self._master_fd: Optional[int] = None
        self._slave_fd: Optional[int] = None
        self._slave_path: Optional[str] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._buf = LineBuffer(cfg.framing)

    def start(self) -> None:
        path = resolve_device(self.cfg.match)
        if not path:
            raise FileNotFoundError(
                f"Reader {self.cfg.name!r}: no matching device "
                f"(match={self.cfg.match.model_dump(exclude_none=True)})"
            )
        self._dev = evdev.InputDevice(path)
        self._dev.grab()
        # Open a pty + symlink to the configured stable path
        self._master_fd, slave_fd = pty.openpty()
        self._slave_path = os.ttyname(slave_fd)
        try:
            mode = int(self.cfg.output.linux.permissions, 8)
        except ValueError:
            mode = 0o666
        os.chmod(self._slave_path, mode)
        # Close OUR slave fd — we never read from it, the consumer
        # (Odoo.ErpNet.FP, Odoo POS, fiscal driver) opens the slave
        # path independently. Keeping our slave fd open made pyserial
        # on the consumer side hit "device reports readiness but no
        # data" because two readers fought for each byte. The pty
        # stays alive as long as master is open — closing slave is
        # safe.
        os.close(slave_fd)
        self._slave_fd = None
        symlink = self.cfg.output.linux.symlink
        if os.path.islink(symlink) or os.path.exists(symlink):
            try:
                os.unlink(symlink)
            except OSError:
                pass
        os.symlink(self._slave_path, symlink)
        _logger.info(
            "Reader %r: %s (%s) → %s (pty %s, mode 0%o)",
            self.cfg.name, self._dev.name, path,
            symlink, self._slave_path, mode,
        )
        self._thread = threading.Thread(
            target=self._loop, name=f"hid2serial[{self.cfg.name}]",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._dev is not None:
            try:
                self._dev.ungrab()
            except Exception:
                pass
            self._dev.close()
            self._dev = None
        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None
        if self._slave_fd is not None:
            try:
                os.close(self._slave_fd)
            except OSError:
                pass
            self._slave_fd = None
        symlink = self.cfg.output.linux.symlink if self.cfg.output.linux else None
        if symlink and (os.path.islink(symlink) or os.path.exists(symlink)):
            try:
                os.unlink(symlink)
            except OSError:
                pass
        _logger.info("Reader %r stopped", self.cfg.name)

    def _loop(self) -> None:
        shift = False
        caps = False
        respect_caps = self.cfg.keymap.caps_lock_strategy == "respect"
        try:
            for ev in self._dev.read_loop():  # type: ignore[union-attr]
                if self._stop.is_set():
                    break
                if ev.type != evdev.ecodes.EV_KEY:
                    continue
                # ev.value: 0=release, 1=press, 2=autorepeat (skip)
                if ev.value == 0:
                    if ev.code in SHIFT_KEYS:
                        shift = False
                    continue
                if ev.value == 2:
                    continue
                code = ev.code
                if code in SHIFT_KEYS:
                    shift = True
                    continue
                if code == KEY_CAPSLOCK:
                    if respect_caps:
                        caps = not caps
                    continue
                if code in TERMINATOR_KEYS:
                    line = self._buf.flush()
                    if line is not None:
                        try:
                            os.write(
                                self._master_fd, line.encode("utf-8")  # type: ignore[arg-type]
                            )
                            _logger.debug(
                                "Reader %r emitted %r", self.cfg.name, line,
                            )
                        except OSError as exc:
                            _logger.warning(
                                "Reader %r pty write failed: %s",
                                self.cfg.name, exc,
                            )
                    continue
                ch = translate(code, shift, caps)
                if ch:
                    self._buf.feed(ch)
        except OSError as exc:
            _logger.warning(
                "Reader %r device error: %s — stopping",
                self.cfg.name, exc,
            )
        except Exception:
            _logger.exception("Reader %r loop crashed", self.cfg.name)
