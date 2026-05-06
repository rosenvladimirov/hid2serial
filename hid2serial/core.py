"""
Cross-platform core for hid2serial.

  * Pydantic config schema
  * US QWERTY keymap (the daemon decodes scancodes itself, ignoring the
    host's system keymap — defensive against bg-cyrillic locales bleeding
    into Cyrillic letter substitution at the OS layer).
  * Line buffer with terminator framing, prefix/suffix strip, max-length
    bound.

Pure-Python; no platform deps. Fully unit-testable.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, ConfigDict


# ─── Configuration schema (matches config.yaml structure) ──────────


class FramingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    terminator: str = "\r\n"           # appended to each emitted scan
    strip_prefix: str = ""              # remove from start of scan if present
    strip_suffix: str = ""              # remove from end (before terminator)
    max_length: int = 4096              # drop oversized scans defensively


class KeymapConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    force: str = "us"                   # us | us_intl
    caps_lock_strategy: str = "ignore"  # ignore | respect


class MatchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name_regex: Optional[str] = None    # regex on device name
    vid: Optional[int] = None           # accepts decimal or 0xNNNN
    pid: Optional[int] = None
    device_path: Optional[str] = None   # explicit /dev/input/eventN
    # Generic catch-all: match the first connected HID-keyboard device
    # that is NOT an internal laptop kbd / trackpad / yubikey / etc.
    # Useful for no-name BLE scanners that don't expose a useful vid /
    # pid / name. Scanners ship with random vendor IDs, so vendor-keyed
    # matching is brittle; empty `match: {}` or `any_external: true`
    # handles 95% of POS deployments without per-device config.
    any_external: bool = False


class LinuxOutputConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symlink: str                        # /dev/ttyV0 or similar stable path
    permissions: str = "0666"           # chmod on the pty target


class WindowsOutputConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    com_pair: list[str] = Field(default_factory=list)  # [CNCA0, COM21]


class OutputConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    linux: Optional[LinuxOutputConfig] = None
    windows: Optional[WindowsOutputConfig] = None


class ReaderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    match: MatchConfig
    output: OutputConfig
    framing: FramingConfig = Field(default_factory=FramingConfig)
    keymap: KeymapConfig = Field(default_factory=KeymapConfig)


class GlobalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    log_level: str = "INFO"
    log_file: Optional[str] = None


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    global_: GlobalConfig = Field(
        default_factory=GlobalConfig, alias="global"
    )
    readers: list[ReaderConfig] = Field(default_factory=list)


# ─── Line buffer (pure logic) ──────────────────────────────────────


class LineBuffer:
    """Accumulates characters until a terminator key fires, then emits
    one well-framed line.

    Pure logic — does not own the keymap or the terminator key set.
    The caller (Linux / Windows backend) feeds chars one at a time and
    calls `flush()` on terminator.
    """

    def __init__(self, framing: FramingConfig) -> None:
        self.framing = framing
        self._buf: list[str] = []

    def feed(self, ch: str) -> None:
        self._buf.append(ch)

    def flush(self) -> Optional[str]:
        """Return one barcode line ready to write to the serial output,
        or None if the line was dropped (oversized / empty)."""
        line = "".join(self._buf)
        self._buf.clear()
        if self.framing.strip_prefix and line.startswith(self.framing.strip_prefix):
            line = line[len(self.framing.strip_prefix):]
        if self.framing.strip_suffix and line.endswith(self.framing.strip_suffix):
            line = line[: -len(self.framing.strip_suffix)]
        line = line.strip()
        if not line:
            return None
        if len(line) > self.framing.max_length:
            return None
        return line + self.framing.terminator


# ─── US QWERTY keymap (independent of host system keymap) ──────────


# Linux evdev keycodes. The daemon translates these directly, bypassing
# any X11/Wayland keymap layer — works correctly on bg-cyrillic locales
# (where the system keymap would otherwise turn ASCII into Cyrillic).
KEY_RESERVED = 0
KEY_1 = 2; KEY_2 = 3; KEY_3 = 4; KEY_4 = 5; KEY_5 = 6
KEY_6 = 7; KEY_7 = 8; KEY_8 = 9; KEY_9 = 10; KEY_0 = 11
KEY_MINUS = 12; KEY_EQUAL = 13
KEY_BACKSPACE = 14; KEY_TAB = 15
KEY_Q = 16; KEY_W = 17; KEY_E = 18; KEY_R = 19; KEY_T = 20
KEY_Y = 21; KEY_U = 22; KEY_I = 23; KEY_O = 24; KEY_P = 25
KEY_LEFTBRACE = 26; KEY_RIGHTBRACE = 27
KEY_ENTER = 28
KEY_LEFTCTRL = 29
KEY_A = 30; KEY_S = 31; KEY_D = 32; KEY_F = 33; KEY_G = 34
KEY_H = 35; KEY_J = 36; KEY_K = 37; KEY_L = 38; KEY_SEMICOLON = 39
KEY_APOSTROPHE = 40; KEY_GRAVE = 41
KEY_LEFTSHIFT = 42; KEY_BACKSLASH = 43
KEY_Z = 44; KEY_X = 45; KEY_C = 46; KEY_V = 47; KEY_B = 48
KEY_N = 49; KEY_M = 50; KEY_COMMA = 51; KEY_DOT = 52; KEY_SLASH = 53
KEY_RIGHTSHIFT = 54
KEY_KPASTERISK = 55
KEY_LEFTALT = 56; KEY_SPACE = 57
KEY_CAPSLOCK = 58
KEY_KP7 = 71; KEY_KP8 = 72; KEY_KP9 = 73; KEY_KPMINUS = 74
KEY_KP4 = 75; KEY_KP5 = 76; KEY_KP6 = 77; KEY_KPPLUS = 78
KEY_KP1 = 79; KEY_KP2 = 80; KEY_KP3 = 81
KEY_KP0 = 82; KEY_KPDOT = 83
KEY_KPENTER = 96; KEY_KPSLASH = 98


KEYMAP_US: dict[int, str] = {
    KEY_1: "1", KEY_2: "2", KEY_3: "3", KEY_4: "4", KEY_5: "5",
    KEY_6: "6", KEY_7: "7", KEY_8: "8", KEY_9: "9", KEY_0: "0",
    KEY_MINUS: "-", KEY_EQUAL: "=",
    KEY_Q: "q", KEY_W: "w", KEY_E: "e", KEY_R: "r", KEY_T: "t",
    KEY_Y: "y", KEY_U: "u", KEY_I: "i", KEY_O: "o", KEY_P: "p",
    KEY_LEFTBRACE: "[", KEY_RIGHTBRACE: "]",
    KEY_A: "a", KEY_S: "s", KEY_D: "d", KEY_F: "f", KEY_G: "g",
    KEY_H: "h", KEY_J: "j", KEY_K: "k", KEY_L: "l", KEY_SEMICOLON: ";",
    KEY_APOSTROPHE: "'", KEY_GRAVE: "`", KEY_BACKSLASH: "\\",
    KEY_Z: "z", KEY_X: "x", KEY_C: "c", KEY_V: "v", KEY_B: "b",
    KEY_N: "n", KEY_M: "m", KEY_COMMA: ",", KEY_DOT: ".", KEY_SLASH: "/",
    KEY_SPACE: " ", KEY_TAB: "\t",
    # Numpad — some scanners use it for digits even with NumLock off
    KEY_KP0: "0", KEY_KP1: "1", KEY_KP2: "2", KEY_KP3: "3", KEY_KP4: "4",
    KEY_KP5: "5", KEY_KP6: "6", KEY_KP7: "7", KEY_KP8: "8", KEY_KP9: "9",
    KEY_KPMINUS: "-", KEY_KPPLUS: "+", KEY_KPDOT: ".",
    KEY_KPSLASH: "/", KEY_KPASTERISK: "*",
}

SHIFT_MAP_US: dict[int, str] = {
    KEY_1: "!", KEY_2: "@", KEY_3: "#", KEY_4: "$", KEY_5: "%",
    KEY_6: "^", KEY_7: "&", KEY_8: "*", KEY_9: "(", KEY_0: ")",
    KEY_MINUS: "_", KEY_EQUAL: "+",
    KEY_LEFTBRACE: "{", KEY_RIGHTBRACE: "}",
    KEY_BACKSLASH: "|", KEY_SEMICOLON: ":",
    KEY_APOSTROPHE: '"', KEY_GRAVE: "~",
    KEY_COMMA: "<", KEY_DOT: ">", KEY_SLASH: "?",
    # Letters: shift = uppercase, handled programmatically
}

TERMINATOR_KEYS = frozenset({KEY_ENTER, KEY_KPENTER})

SHIFT_KEYS = frozenset({KEY_LEFTSHIFT, KEY_RIGHTSHIFT})


def translate(scancode: int, shift: bool, caps: bool) -> Optional[str]:
    """Translate one scancode into a printable character, applying the
    shift / caps-lock state. Returns None for non-printable keys."""
    if shift:
        ch = SHIFT_MAP_US.get(scancode)
        if ch is not None:
            return ch
        # Letters: use unshifted, then uppercase
        base = KEYMAP_US.get(scancode)
        return base.upper() if base and base.isalpha() else base
    base = KEYMAP_US.get(scancode)
    if base is None:
        return None
    if caps and base.isalpha():
        return base.upper()
    return base
