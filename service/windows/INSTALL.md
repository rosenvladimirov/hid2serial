# hid2serial — Windows install guide

> Status: **v0.2-dev**, code-complete, awaiting hardware test.

## Prerequisites

1. **Python 3.11+** (64-bit) — install from [python.org](https://www.python.org/downloads/) with "Add to PATH" enabled.
2. **com0com** — virtual COM port pair driver. Download:
   - <https://sourceforge.net/projects/com0com/files/com0com/3.0.0.0/>
   - Run installer as Administrator.
   - Reboot after install (Windows requires it for kernel-mode driver).
   - Open `Setup` from Start Menu and create a port pair, e.g.:
     - Side A: `CNCA0` (the daemon writes to this)
     - Side B: `COM21` (Odoo POS / fiscal driver opens this)
   - Tick **"use Ports class"** on the side that the consumer opens
     (`COM21`) so it appears in standard COM-port enumeration.
3. **Administrator privileges** — needed once to register the service
   and add the user to the appropriate group.

## Install

1. Download `hid2serial-<version>.zip` from
   <https://github.com/rosenvladimirov/hid2serial/releases>.
2. Extract and open an Administrator command prompt in the extracted folder.
3. Run:
   ```cmd
   install.bat
   ```
   This:
   - `pip install` of `hid2serial` + `pywin32` + `pystray` + `pillow` + `pyserial`
   - Registers the Windows service `hid2serial`
   - Copies the example config to `%PROGRAMDATA%\hid2serial\config.yaml`
   - Adds the tray to your user's Startup folder

## Configure

Edit `%PROGRAMDATA%\hid2serial\config.yaml` and set the `output.windows.com_pair`
field to your com0com pair:

```yaml
readers:
  - name: scanner1
    match:
      any_external: true     # or vid + pid + name_regex for specific
    output:
      windows:
        com_pair: ["CNCA0", "COM21"]   # daemon writes CNCA0; consumer opens COM21
    framing:
      terminator: "\r\n"
      max_length: 4096
    keymap:
      force: us
      caps_lock_strategy: ignore
```

## Start

Either via the tray (right-click the keyboard icon → **Start redirect**)
or from an Admin prompt:

```cmd
sc start hid2serial
```

## Verify

1. The tray icon should turn green (● Running).
2. Open Notepad — scanned barcodes should NOT type into it (the
   daemon's RawInput suppression hook intercepts them).
3. Open `COM21` in any serial-terminal app (e.g. PuTTY, RealTerm) at
   9600 baud, scan a barcode — the decoded value followed by `\r\n`
   should appear in the terminal.
4. Toggle the tray to **Stop redirect** — scans go back to typing into
   the focused window normally.

## Uninstall

Run `uninstall.bat` from an Admin prompt. com0com stays installed
(use its own uninstaller from Add/Remove Programs).

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Service won't start | pywin32 post-install not run | `python -m pywin32_postinstall -install` |
| No barcodes in COM21 | wrong com0com pair side | check Setup tool — write to side A, read side B |
| Scans still type into Notepad | suppression hook not loaded | check service is running as `LocalSystem` (default) |
| Multiple scanners conflict | only one reader configured | add a second `readers:` entry with own com_pair |
| Scanner not detected | Bluetooth not paired or USB driver missing | pair / install vendor driver first |

## Why a separate hid2serial daemon (vs. native Windows COM mode)?

Many cheap BLE / OEM scanners don't expose a serial / CDC ACM mode at
all — they only enumerate as a HID keyboard. This daemon takes those
keystrokes and re-emits them on a serial port, which is what every
fiscal-printer / POS / inventory app expects. For scanners that DO
support serial mode (e.g. Honeywell Voyager XP 1470g via the "USB
Serial Driver Required" config barcode), skip the daemon entirely and
configure the consumer to open `COMx` directly.
