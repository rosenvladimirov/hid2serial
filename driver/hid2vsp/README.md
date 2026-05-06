# hid2vsp — User-mode Virtual Serial Port pair driver for Windows

Sub-project of [`hid2serial`](../../). Provides null-modem-style virtual
COM port pairs on Windows so consumer applications (industrial testers,
legacy fiscal-printer SDKs, OPOS components, in-house tools) can open a
familiar `COM31` / `COM32` and read what the daemon writes — without a
physical RS-232 cable and without depending on the unmaintained com0com
3.0 driver from 2017.

This is the **Windows-side companion** to the Linux pty / symlink path
in `hid2serial/linux.py`. On Linux we use `pty.openpty()` + a stable
`/dev/ttyV0` symlink; on Windows we use this UMDF v2 driver.

## Why a new driver

| Existing option | Status |
|---|---|
| com0com 3.0 (vfrolov, 2017) | dead since 2017, cross-signed with old SHA-1 chain, **fails on Win 11 24H2 with HVCI on (Code 52)** |
| `pbatard/com0com` | does not exist (referenced sometimes online — confirmed 2026-05-06 not real) |
| HHD Free Virtual Serial Ports | user-mode UMDF, GlobalSign-signed, but **free tier loses pairs on reboot** + no custom names + no multi-pair |
| Eltima Virtual Serial Port Driver | $160/yr per machine, closed-source |
| Microsoft VirtualSerial2 sample | "not intended for production" (per README) |

None fit a free + open-source + Win 11 24H2 + HVCI-on industrial
deployment. So we build our own — UMDF v2 user-mode, MIT/GPL licensed,
distributed as part of the hid2serial Windows installer.

## Architecture

```
┌────────────────────────────────────────────────────────┐
│ USER SPACE                                             │
│ ┌──────────┐                              ┌──────────┐ │
│ │ Tester / │ Serial.Open("COM31")         │ hid2-    │ │
│ │ legacy   │─────────────────────────────▶│ serial   │ │
│ │ COM app  │                              │ daemon   │ │
│ └──────────┘                              └──────────┘ │
│      ▲ buffer reads                Write to buffer ▲   │
│      └────────────────┬────────────────────────────┘   │
│                       │                                │
│                ┌──────▼────────┐                       │
│                │ hid2vsp.dll   │ user-mode driver      │
│                │ (UMDF v2)     │ ring buffer per pair  │
│                │  + .inf       │ + IOCTL_SERIAL_*      │
│                └──────┬────────┘                       │
├───────────────────────┼────────────────────────────────┤
│ KERNEL SPACE          ▼                                │
│         ┌──────────────────────────┐                   │
│         │ Microsoft UMDF Reflector │  built-in         │
│         │  (HVCI-trusted)          │  on Win 8.1+      │
│         └──────────────────────────┘                   │
└────────────────────────────────────────────────────────┘
```

**Key properties:**

- Vendor code runs **entirely in user mode** — no kernel responsibility,
  no BSoD risk
- Microsoft UMDF reflector in kernel is built-in and HVCI-trusted from
  the OS install — we don't ship any kernel binary
- Standard Windows code-signing cert is sufficient (UMDF v2 does not
  require WHQL or EV)
- Each pair is one bus-enumerated device with two child function devices
  (`COMx` and `COMy`), sharing a thread-safe ring buffer

## Phased plan (4 weeks)

| Week | Deliverable | Verification |
|---|---|---|
| **1** | Hello-world single-port UMDF v2 driver: build, install, enumerate in Device Manager. Open / close / write through `COM31` returns success but discards data | `pyserial.Serial("COM31")` opens; `Get-CimInstance Win32_SerialPort` shows it |
| **2** | Pair support: write on COM31 → read appears on COM32 (and vice versa). Standard `IOCTL_SERIAL_SET_BAUD_RATE`, `IOCTL_SERIAL_SET_LINE_CONTROL`, `IOCTL_SERIAL_GET_COMMSTATUS`, `IOCTL_SERIAL_PURGE`, `IOCTL_SERIAL_WAIT_ON_MASK` | Echo test from PowerShell terminals; `pyserial` round-trip; baud-rate reported correctly |
| **3** | Multi-pair + registry persist + RTS/CTS/DTR/DSR signaling between endpoints + `hid2vspctl.exe` CLI (`create COM31 COM32`, `destroy COM31`, `list`) | Reboot test — pairs survive; 3 simultaneous pairs work independently |
| **4** | Test-signing for dev, NSIS integration, install/uninstall verification, HVCI=on smoke test, barcode E2E | barcode scanner → daemon → COM31 → tester sees the line on COM32 |

## Dev environment (in the Win 11 VM)

Required (all free):

- Windows 11 Pro 24H2 (Build 26100+) — [download from Microsoft](https://www.microsoft.com/software-download/windows11)
- Visual Studio 2022 Community Edition with workload "Desktop development with C++" + spectre-mitigated libs
- Windows 11 SDK (latest, comes with VS Installer)
- WDK 10 — Windows Driver Kit, install via VS Installer's "Individual components" search "WDK"
- Git for Windows
- Python 3.12+ (for build / packaging scripts and pyserial-based tests)

Test-signing setup (one-time, on dev VM only):

```cmd
:: Run as Administrator
bcdedit /set testsigning on
shutdown /r /t 0
```

Generate self-signed cert for dev signing:

```cmd
makecert -r -pe -ss MyCertStore -n "CN=hid2vsp Dev Test" hid2vsp-dev.cer
```

Production signing path is deliberately deferred — see [docs/signing.md](docs/signing.md).

## Dev workflow

The Claude session in the VM should:

1. Read this README and [docs/dev_plan.md](docs/dev_plan.md) first.
2. Clone the Microsoft `Windows-driver-samples` repo, locate
   `serial/VirtualSerial2`, and use it as the starting point.
3. Build the unmodified sample first to confirm the toolchain works.
4. Then iterate week by week — see `docs/dev_plan.md`.
5. Commit progress to a feature branch on the parent `hid2serial` repo.

The parent Linux repo at `~/Проекти/odoo/iot/hid2serial/` already has
the Linux backend. Claude in the VM should pull from
`https://github.com/rosenvladimirov/hid2serial` directly (not via shared
folder — Windows path conventions differ enough that local git is
cleaner).

## License

GPL-3.0-or-later, matching the parent project. The reasoning behind the
license choice (vs LGPL or Apache) is in `docs/license_decision.md`.
