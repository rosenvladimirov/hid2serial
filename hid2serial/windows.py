"""
Windows backend — RawInput-based HID scanner reader.

Architecture:
    [HID scanner] → RawInput (per-device subscription) → hidden window
                  → WM_INPUT message handler → keymap → LineBuffer
                  → write to com0com side A → consumer reads com0com
                                                side B as a regular COM
                                                port (pyserial / Odoo POS
                                                / fiscal driver)

Why RawInput (not raw HID via HIDAPI): RawInput is built into Win32, no
external library install. Per-device hDevice filter lets us scope our
reader to ONE specific scanner — other keyboards on the system stay
unaffected. The legacy WM_KEYDOWN events are still emitted by the
kernel (so the scanner ALSO types to the focused window unless
suppression is enabled — see `_suppress_keyboard_hook`).

When `suppress: true` (default ON for production), a low-level
`WH_KEYBOARD_LL` hook drops the legacy events whose scancode + timing
match a recent WM_INPUT from our scanner — typically a 5 ms window
after the RawInput message arrives. This is how every Windows POS
overlay does it; the timing window has to be small to avoid swallowing
legitimate keystrokes from other keyboards typing at the same time.

com0com prerequisite — see `service/windows/install_com0com.bat`. The
daemon does NOT install the driver itself (kernel-mode driver install
needs admin + reboot). It expects a COM pair to already exist; we
write to side A and the consumer opens side B as a normal COM port.

Status: code-complete v0.2-dev — awaiting Windows hardware test.
"""

from __future__ import annotations

import ctypes
import logging
import os
import re
import threading
import time
from collections import deque
from ctypes import wintypes
from typing import Optional

import json
import queue
import urllib.error
import urllib.request

try:
    import serial  # pyserial — only used by the legacy/deprecated com0com path
except ImportError:  # pragma: no cover
    serial = None

from .core import (
    KEY_CAPSLOCK,
    KEY_ENTER,
    KEY_KPENTER,
    KEY_LEFTSHIFT,
    KEY_RIGHTSHIFT,
    LineBuffer,
    ReaderConfig,
    SHIFT_KEYS,
    TERMINATOR_KEYS,
    translate,
)

_logger = logging.getLogger(__name__)


# ─── Win32 constants ────────────────────────────────────────────────


HID_USAGE_PAGE_GENERIC = 0x01
HID_USAGE_GENERIC_KEYBOARD = 0x06

RIDEV_INPUTSINK = 0x00000100   # receive WM_INPUT even when not focused
RIDEV_DEVNOTIFY = 0x00002000   # receive arrival/removal notifications
RIDEV_REMOVE = 0x00000001

WM_INPUT = 0x00FF
WM_INPUT_DEVICE_CHANGE = 0x00FE

RID_INPUT = 0x10000003
RID_HEADER = 0x10000005

RIM_TYPEKEYBOARD = 1

WH_KEYBOARD_LL = 13
HC_ACTION = 0
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

WS_OVERLAPPEDWINDOW = 0x00CF0000
HWND_MESSAGE = -3

RIDI_DEVICENAME = 0x20000007


# ─── Win32 structs ──────────────────────────────────────────────────


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", wintypes.USHORT),
        ("usUsage", wintypes.USHORT),
        ("dwFlags", wintypes.DWORD),
        ("hwndTarget", wintypes.HWND),
    ]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ("dwType", wintypes.DWORD),
        ("dwSize", wintypes.DWORD),
        ("hDevice", wintypes.HANDLE),
        ("wParam", wintypes.WPARAM),
    ]


class RAWKEYBOARD(ctypes.Structure):
    _fields_ = [
        ("MakeCode", wintypes.USHORT),
        ("Flags", wintypes.USHORT),
        ("Reserved", wintypes.USHORT),
        ("VKey", wintypes.USHORT),
        ("Message", wintypes.UINT),
        ("ExtraInformation", wintypes.ULONG),
    ]


class RAWINPUT_DATA(ctypes.Union):
    _fields_ = [
        ("keyboard", RAWKEYBOARD),
    ]


class RAWINPUT(ctypes.Structure):
    _fields_ = [
        ("header", RAWINPUTHEADER),
        ("data", RAWINPUT_DATA),
    ]


class RID_DEVICE_INFO_KEYBOARD(ctypes.Structure):
    _fields_ = [
        ("dwType", wintypes.DWORD),
        ("dwSubType", wintypes.DWORD),
        ("dwKeyboardMode", wintypes.DWORD),
        ("dwNumberOfFunctionKeys", wintypes.DWORD),
        ("dwNumberOfIndicators", wintypes.DWORD),
        ("dwNumberOfKeysTotal", wintypes.DWORD),
    ]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


user32 = ctypes.WinDLL("user32") if os.name == "nt" else None
kernel32 = ctypes.WinDLL("kernel32") if os.name == "nt" else None


# ─── Device discovery ──────────────────────────────────────────────


def list_raw_keyboards() -> list[dict]:
    """Enumerate keyboard-class HID devices via RawInput. Returns one
    dict per device with name + device path (e.g. \\\\?\\HID#VID_0C2E&PID_0B6A...)."""
    if os.name != "nt" or user32 is None:
        return []
    # Two-pass: first call gets count, second call fills the array.
    GetRawInputDeviceList = user32.GetRawInputDeviceList
    GetRawInputDeviceList.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.UINT), wintypes.UINT,
    ]
    GetRawInputDeviceList.restype = wintypes.UINT

    class RAWINPUTDEVICELIST(ctypes.Structure):
        _fields_ = [("hDevice", wintypes.HANDLE), ("dwType", wintypes.DWORD)]

    count = wintypes.UINT(0)
    GetRawInputDeviceList(None, ctypes.byref(count), ctypes.sizeof(RAWINPUTDEVICELIST))
    if count.value == 0:
        return []
    arr_t = RAWINPUTDEVICELIST * count.value
    arr = arr_t()
    GetRawInputDeviceList(arr, ctypes.byref(count), ctypes.sizeof(RAWINPUTDEVICELIST))

    out: list[dict] = []
    GetRawInputDeviceInfoW = user32.GetRawInputDeviceInfoW
    for i in range(count.value):
        d = arr[i]
        if d.dwType != RIM_TYPEKEYBOARD:
            continue
        # Get device name (HID interface path, includes VID/PID/SN)
        size = wintypes.UINT(0)
        GetRawInputDeviceInfoW(
            d.hDevice, RIDI_DEVICENAME, None, ctypes.byref(size),
        )
        if size.value == 0:
            continue
        buf = ctypes.create_unicode_buffer(size.value)
        GetRawInputDeviceInfoW(
            d.hDevice, RIDI_DEVICENAME, buf, ctypes.byref(size),
        )
        path = buf.value
        vid, pid = _parse_vid_pid(path)
        out.append({
            "h_device": d.hDevice,
            "path": path,
            "vid": vid,
            "pid": pid,
            "name": path,  # Win32 doesn't expose the friendly product
                            # string for keyboard-class HID at this layer
        })
    return out


def _parse_vid_pid(path: str) -> tuple[int, int]:
    """Extract VID/PID from a HID interface path like
    \\\\?\\HID#VID_0C2E&PID_0B6A&MI_00#7&...&0000"""
    m = re.search(r"VID_([0-9A-F]+)&PID_([0-9A-F]+)", path, re.I)
    if not m:
        return 0, 0
    return int(m.group(1), 16), int(m.group(2), 16)


def resolve_h_device(cfg_match) -> Optional[int]:
    """Find the first RawInput hDevice matching config criteria. Returns
    the Win32 HANDLE (an int)."""
    devices = list_raw_keyboards()
    if not devices:
        return None
    if cfg_match.device_path:
        for d in devices:
            if d["path"] == cfg_match.device_path:
                return d["h_device"]
        return None
    name_re = re.compile(cfg_match.name_regex, re.I) if cfg_match.name_regex else None
    no_specific = (
        cfg_match.vid is None and cfg_match.pid is None
        and not cfg_match.name_regex and not cfg_match.device_path
    )
    for d in devices:
        if cfg_match.vid is not None and d["vid"] != cfg_match.vid:
            continue
        if cfg_match.pid is not None and d["pid"] != cfg_match.pid:
            continue
        if name_re and not name_re.search(d["path"] or ""):
            continue
        if cfg_match.any_external or no_specific:
            # Filter out laptop's internal keyboard — Windows reports
            # it with "ACPI" or "i8042" in its path.
            if "ACPI" in d["path"] or "i8042" in d["path"]:
                continue
        return d["h_device"]
    return None


# ─── Make-code → evdev-compatible scancode ─────────────────────────


# Win32 RAWKEYBOARD.MakeCode is the PS/2 set-1 scancode. Linux evdev
# uses a different numbering (set-2-like). For the keys we care about
# (US layout digits + letters + Enter + numpad), set-1 codes match
# Linux's KEY_* values 1:1 — we just shift the table.
_PS2_TO_EVDEV = {
    # set-1 → evdev (KEY_*); identity for most rows.
    # Build directly from core.py's KEYMAP_US — same numbers.
    i: i for i in range(128)
}


# ─── Keyboard reader (RawInput) ────────────────────────────────────


class _MessageWindow:
    """Hidden message-only window that receives WM_INPUT for our
    scanner. Runs its own message pump in a daemon thread."""

    WND_CLASS_NAME = "hid2serial_msgwnd"

    def __init__(self, on_input):
        self._on_input = on_input
        self._hwnd: Optional[int] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._stop = False

    def start(self) -> int:
        self._thread = threading.Thread(
            target=self._run, name="hid2serial-rawinput",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=5):
            raise RuntimeError("Message window failed to start")
        return self._hwnd  # type: ignore[return-value]

    def stop(self):
        self._stop = True
        if self._hwnd:
            user32.PostMessageW(self._hwnd, 0x0012, 0, 0)  # WM_QUIT
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self):
        # Register the window class
        WNDPROC = ctypes.WINFUNCTYPE(
            ctypes.c_long, wintypes.HWND, wintypes.UINT,
            wintypes.WPARAM, wintypes.LPARAM,
        )

        def _wndproc(hwnd, msg, wparam, lparam):
            if msg == WM_INPUT:
                self._on_input(lparam)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wndproc = WNDPROC(_wndproc)

        class WNDCLASS(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        wc = WNDCLASS()
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = kernel32.GetModuleHandleW(None)
        wc.lpszClassName = self.WND_CLASS_NAME
        user32.RegisterClassW(ctypes.byref(wc))

        self._hwnd = user32.CreateWindowExW(
            0, self.WND_CLASS_NAME, None, 0, 0, 0, 0, 0,
            HWND_MESSAGE, None, wc.hInstance, None,
        )
        self._ready.set()

        msg = wintypes.MSG()
        while not self._stop:
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r in (0, -1):
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))


class _SuppressionHook:
    """Low-level keyboard hook (WH_KEYBOARD_LL) that drops legacy
    WM_KEYDOWN events whose scancode + timing match a recent WM_INPUT
    from our scanner. Windows ALWAYS emits the legacy event in addition
    to RawInput — without this hook the scanner also types into the
    focused window."""

    def __init__(self, recent_scancodes: deque):
        self._recent = recent_scancodes
        self._hHook: Optional[int] = None
        self._proc = None

    def install(self):
        HOOKPROC = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_int,
            wintypes.WPARAM, wintypes.LPARAM,
        )

        def _proc(nCode, wParam, lParam):
            if nCode == HC_ACTION and wParam in (
                WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP,
            ):
                kb = ctypes.cast(
                    lParam, ctypes.POINTER(KBDLLHOOKSTRUCT),
                ).contents
                # Check if we recently saw this scancode from our reader
                now_ms = time.monotonic() * 1000
                for stamp_ms, sc in list(self._recent):
                    if (now_ms - stamp_ms) <= 50 and sc == kb.scanCode:
                        # Suppress — return non-zero
                        return 1
            return user32.CallNextHookEx(0, nCode, wParam, lParam)

        self._proc = HOOKPROC(_proc)
        self._hHook = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._proc, kernel32.GetModuleHandleW(None), 0,
        )
        if not self._hHook:
            raise OSError("SetWindowsHookEx failed")

    def uninstall(self):
        if self._hHook:
            user32.UnhookWindowsHookEx(self._hHook)
            self._hHook = None


class _HttpSink:
    """Background worker that POSTs barcode lines to a proxy endpoint.

    Decouples the WM_INPUT message handler from network latency so a
    slow / unreachable proxy never blocks the keyboard hook. Drops
    scans on the floor (with a warning) if the queue overflows — the
    barcode is also captured in `hid2serial.log` so nothing is lost.
    """

    _SENTINEL = object()

    def __init__(
        self,
        url: str,
        timeout_s: float,
        headers: dict,
        verify_tls: bool,
        reader_name: str,
    ) -> None:
        self._url = url
        self._timeout = timeout_s
        self._headers = {"Content-Type": "application/json", **headers}
        self._verify_tls = verify_tls
        self._name = reader_name
        self._q: "queue.Queue[object]" = queue.Queue(maxsize=200)
        self._thread = threading.Thread(
            target=self._loop,
            name=f"HttpSink[{reader_name}]",
            daemon=True,
        )
        # ssl context — only consulted when target is https
        self._ssl_ctx = None
        if url.lower().startswith("https://") and not verify_tls:
            import ssl
            self._ssl_ctx = ssl.create_default_context()
            self._ssl_ctx.check_hostname = False
            self._ssl_ctx.verify_mode = ssl.CERT_NONE

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._q.put(self._SENTINEL)
        self._thread.join(timeout=2.0)

    def submit(self, text: str) -> None:
        try:
            self._q.put_nowait(text)
        except queue.Full:
            _logger.warning(
                "Reader %r: HTTP sink queue full, dropping scan %r",
                self._name, text,
            )

    def _loop(self) -> None:
        while True:
            item = self._q.get()
            if item is self._SENTINEL:
                return
            try:
                self._post(item)  # type: ignore[arg-type]
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "Reader %r: HTTP sink unexpected error", self._name,
                )

    def _post(self, text: str) -> None:
        body = json.dumps({"text": text}).encode("utf-8")
        req = urllib.request.Request(
            self._url, data=body, headers=self._headers, method="POST",
        )
        try:
            with urllib.request.urlopen(
                req, timeout=self._timeout, context=self._ssl_ctx,
            ) as resp:
                if resp.status >= 400:
                    _logger.warning(
                        "Reader %r: proxy returned %d for scan %r",
                        self._name, resp.status, text,
                    )
        except urllib.error.URLError as exc:
            _logger.warning(
                "Reader %r: proxy POST failed (%s) — scan %r dropped",
                self._name, exc, text,
            )


class ReaderRunner:
    """Windows ReaderRunner — RawInput + line buffer + HTTP sink.

    Mirror of Linux ReaderRunner from `linux.py` but Win-specific:
    instead of evdev grab + pty, we register RawInput for the scanner's
    HID handle, run a message pump, and POST each barcode line to the
    proxy's external-reader inject endpoint. No kernel driver, no
    virtual COM port, no signing concerns.

    The legacy com0com COM-pair output mode is kept for backward compat
    but is not the recommended path and is not exercised by the
    Windows installer's default config.
    """

    def __init__(self, cfg: ReaderConfig) -> None:
        if os.name != "nt":
            raise RuntimeError("Windows backend can only run on Windows")
        out = cfg.output
        if out.http is None and (out.windows is None or not out.windows.com_pair):
            raise ValueError(
                f"Reader {cfg.name!r}: configure either output.http.url "
                f"(recommended) or output.windows.com_pair (legacy com0com)"
            )
        self.cfg = cfg
        self._mode: str = "http" if out.http is not None else "com"
        self._comx: Optional[str] = (
            out.windows.com_pair[0]
            if self._mode == "com" and out.windows
            else None
        )
        self._h_device: Optional[int] = None
        self._win: Optional[_MessageWindow] = None
        self._hook: Optional[_SuppressionHook] = None
        self._serial: Optional["serial.Serial"] = None
        self._http: Optional[_HttpSink] = None
        self._buf = LineBuffer(cfg.framing)
        self._recent_scancodes: deque = deque(maxlen=64)
        self._shift = False
        self._caps = False

    def start(self) -> None:
        self._h_device = resolve_h_device(self.cfg.match)
        if not self._h_device:
            raise FileNotFoundError(
                f"Reader {self.cfg.name!r}: no matching HID keyboard"
            )
        if self._mode == "http":
            cfg_http = self.cfg.output.http  # type: ignore[union-attr]
            self._http = _HttpSink(
                url=cfg_http.url,
                timeout_s=cfg_http.timeout_s,
                headers=cfg_http.headers,
                verify_tls=cfg_http.verify_tls,
                reader_name=self.cfg.name,
            )
            self._http.start()
        else:
            if serial is None:
                raise RuntimeError("pyserial is required for the com0com path")
            self._serial = serial.Serial(
                self._comx, baudrate=9600, timeout=0,
            )

        # Spin up message-only window first so RegisterRawInputDevices
        # has a valid hwnd target.
        self._win = _MessageWindow(self._on_wm_input)
        hwnd = self._win.start()

        rid = RAWINPUTDEVICE(
            usUsagePage=HID_USAGE_PAGE_GENERIC,
            usUsage=HID_USAGE_GENERIC_KEYBOARD,
            dwFlags=RIDEV_INPUTSINK | RIDEV_DEVNOTIFY,
            hwndTarget=hwnd,
        )
        rids = (RAWINPUTDEVICE * 1)(rid)
        if not user32.RegisterRawInputDevices(
            rids, 1, ctypes.sizeof(RAWINPUTDEVICE),
        ):
            raise OSError("RegisterRawInputDevices failed")

        # Install suppression hook so the scanner doesn't ALSO type into
        # the focused window. Toggling redirect off via tray will stop
        # the daemon and the hook unloads.
        self._hook = _SuppressionHook(self._recent_scancodes)
        self._hook.install()

        _logger.info(
            "Reader %r: hDevice=0x%x → %s (suppress=on)",
            self.cfg.name, self._h_device,
            f"HTTP {self.cfg.output.http.url}" if self._mode == "http"
            else f"COM {self._comx}",
        )

    def stop(self) -> None:
        if self._hook:
            try:
                self._hook.uninstall()
            except Exception:
                pass
            self._hook = None
        if self._win:
            self._win.stop()
            self._win = None
        if self._http:
            try:
                self._http.stop()
            except Exception:
                pass
            self._http = None
        if self._serial:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None

    def _on_wm_input(self, lparam: int) -> None:
        """Handle one WM_INPUT message — read RAWINPUT, decode, buffer,
        flush on terminator."""
        size = wintypes.UINT(0)
        user32.GetRawInputData(
            lparam, RID_INPUT, None, ctypes.byref(size),
            ctypes.sizeof(RAWINPUTHEADER),
        )
        if size.value == 0:
            return
        buf = (ctypes.c_byte * size.value)()
        user32.GetRawInputData(
            lparam, RID_INPUT, buf, ctypes.byref(size),
            ctypes.sizeof(RAWINPUTHEADER),
        )
        ri = ctypes.cast(buf, ctypes.POINTER(RAWINPUT)).contents
        if ri.header.dwType != RIM_TYPEKEYBOARD:
            return
        if ri.header.hDevice != self._h_device:
            return  # not our scanner

        kb = ri.data.keyboard
        scancode = kb.MakeCode
        # Flags & 1 == release. We only emit on press.
        is_break = bool(kb.Flags & 0x01)

        if is_break:
            if scancode in SHIFT_KEYS:
                self._shift = False
            return

        # Track for suppression hook
        self._recent_scancodes.append((time.monotonic() * 1000, scancode))

        if scancode in SHIFT_KEYS:
            self._shift = True
            return
        if scancode == KEY_CAPSLOCK:
            if self.cfg.keymap.caps_lock_strategy == "respect":
                self._caps = not self._caps
            return
        if scancode in TERMINATOR_KEYS:
            line = self._buf.flush()
            if line is None:
                return
            if self._http is not None:
                # Strip any trailing terminator the LineBuffer kept;
                # the proxy adds its own newline semantics.
                self._http.submit(line.rstrip("\r\n"))
                _logger.debug(
                    "Reader %r emitted %r → HTTP queue",
                    self.cfg.name, line,
                )
            elif self._serial is not None:
                try:
                    self._serial.write(line.encode("utf-8"))
                    self._serial.flush()
                    _logger.debug(
                        "Reader %r emitted %r → COM",
                        self.cfg.name, line,
                    )
                except Exception as exc:
                    _logger.warning(
                        "Reader %r COM write failed: %s",
                        self.cfg.name, exc,
                    )
            return

        ch = translate(scancode, self._shift, self._caps)
        if ch:
            self._buf.feed(ch)


# ─── Public API helpers (used by cli + service) ────────────────────


def list_keyboard_devices() -> list[dict]:
    """Public alias matching linux.py — returns list of HID-keyboard
    devices visible to RawInput, with VID/PID/path/name."""
    return list_raw_keyboards()
