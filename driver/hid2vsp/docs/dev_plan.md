# hid2vsp — Detailed dev plan

> Read this AFTER `../README.md`. Stay disciplined per-phase — do not
> jump ahead. Each phase has a clear "done" gate.

## Phase 1 — Hello-world single port (week 1)

### Goal
A single virtual `COM31` shows up in Device Manager, opens via
pyserial, accepts writes (discards them), and responds to standard
serial IOCTLs without crashing.

### Steps

1. `git clone https://github.com/microsoft/Windows-driver-samples.git C:\src\winsamples`
2. Open `C:\src\winsamples\serial\VirtualSerial2\VirtualSerial2.sln` in Visual Studio 2022
3. Build for `Debug | x64`. Confirm `VirtualSerial2.dll`, `.inf`, and `.cat` files appear in the output dir.
4. Install the unmodified sample with `pnputil`:
   ```cmd
   pnputil /add-driver VirtualSerial2.inf /install
   ```
5. Verify `COM3` (or whatever it auto-assigned) appears in Device Manager → Ports (COM & LPT).
6. Open it from Python:
   ```python
   import serial
   s = serial.Serial("COM3", baudrate=9600, timeout=1)
   s.write(b"hello")
   s.close()
   ```
7. **Now fork** — copy the sample to `driver/hid2vsp/src/` and rename
   the project to `hid2vsp`. Update INF, hardware ID, vendor /
   description fields:
   - INF: `Provider = "hid2serial project"`
   - DeviceDesc: `"hid2vsp Virtual COM Pair"`
   - Hardware ID: `Root\hid2vsp` (root-enumerated device — no physical bus)
8. Re-build, re-install, verify it appears with the new description.

### Done gate
- `pnputil /enum-drivers | findstr hid2vsp` lists our INF
- pyserial open/write/close round-trip succeeds without errors

## Phase 2 — Pair support (week 2)

### Goal
Two ports per pair (COM31 / COM32). Write on one → read appears on the
other. Standard IOCTLs respond correctly.

### Key changes from the sample
The VirtualSerial2 sample is a **single device** that echoes its own
writes. We need a **bus driver** that enumerates **two child devices
per pair**, sharing a ring buffer.

1. Refactor the sample so the WDFDEVICE is the bus (pair container).
2. Use `WdfFdoInitOpenRegistryKey` to read pair config from
   `HKLM\SOFTWARE\hid2serial\hid2vsp\pairs\<id>` →
   `{ portA: "COM31", portB: "COM32" }`.
3. For each pair, child-enumerate two PDOs via
   `WdfPdoInitAllocate` + `WdfPdoInitAssignDeviceID`. Hardware IDs:
   `Root\hid2vsp\PortA` and `Root\hid2vsp\PortB`.
4. Each PDO function driver opens a handle to the **same** ring buffer
   (allocated in the bus driver, not per-port). Writes go into the
   buffer tagged with the source PDO; reads block on a `WDF_REQUEST`
   queue waiting for data tagged from the OTHER PDO.
5. Implement these IOCTLs (in `EvtIoDeviceControl`):
   - `IOCTL_SERIAL_SET_BAUD_RATE` — store, no actual hardware effect
   - `IOCTL_SERIAL_GET_BAUD_RATE` — return stored
   - `IOCTL_SERIAL_SET_LINE_CONTROL` / `_GET_LINE_CONTROL` — same
   - `IOCTL_SERIAL_GET_COMMSTATUS` — return synthetic status (no errors)
   - `IOCTL_SERIAL_PURGE` — clear buffer, complete pending reads with TIMEOUT
   - `IOCTL_SERIAL_WAIT_ON_MASK` / `IOCTL_SERIAL_SET_WAIT_MASK` — basic event mask support
   - `IOCTL_SERIAL_SET_TIMEOUTS` / `_GET_TIMEOUTS` — honor in IRP_MJ_READ paths

### Done gate
- Echo test: PuTTY connected to COM31, second PuTTY to COM32. Type in one → see chars in the other (both directions).
- pyserial round-trip:
  ```python
  a = serial.Serial("COM31", 9600); b = serial.Serial("COM32", 9600)
  a.write(b"abc\r\n"); time.sleep(0.1)
  assert b.read(5) == b"abc\r\n"
  ```

## Phase 3 — Multi-pair, persist, signaling (week 3)

### Multi-pair
- Bus driver iterates over `HKLM\SOFTWARE\hid2serial\hid2vsp\pairs\*`
  on startup, child-enumerates all defined pairs.
- New CLI tool `tools/hid2vspctl.exe` (C++ or pre-built Python EXE
  via PyInstaller — both fine):
  ```
  hid2vspctl create COM31 COM32       :: writes to registry, triggers re-enum
  hid2vspctl list                      :: prints all pairs + state
  hid2vspctl destroy COM31             :: removes pair from registry
  ```
- Trigger re-enumeration with `WdfDeviceUpdateInterruptOnKnownDevice`
  or programmatic `Cm_Reenumerate_Device`.

### Registry persistence
- Pairs survive reboot (no "lose state" failure mode like HHD Free tier).
- Pair config is the **only** state — the buffer is in-memory and starts empty after reboot.

### Modem signaling
- IOCTL_SERIAL_SET_DTR / CLR_DTR / SET_RTS / CLR_RTS on PDO A → flip
  bits visible to PDO B via `IOCTL_SERIAL_GET_MODEMSTATUS`.
- Simulate cross-wired null-modem: A's DTR → B's DSR; A's RTS → B's CTS.
- Used by industrial testers that wait for DSR=1 before reading.

### Done gate
- `hid2vspctl create COM50 COM51` → pair appears immediately
- Reboot → both pairs (default + COM50/51) come back
- DTR toggle on COM31 → COM32 sees DSR change

## Phase 4 — Sign + integrate + test (week 4)

### Self-signed for dev
- `MakeCert.exe` for self-signed cert
- `Inf2Cat.exe` to generate catalog
- `SignTool.exe sign /v /s My /n "hid2vsp Dev Test" /fd sha256 hid2vsp.dll`
- Document in `docs/install.md` that prod machines need either:
  - Test mode + import cert, OR
  - WHQL submission (deferred — see signing.md)

### NSIS integration
- New section in `packaging/windows/installer.nsi`:
  ```nsi
  Section "hid2vsp Driver (virtual COM pair)" SEC_DRIVER
      SetOutPath "$INSTDIR\driver"
      File "..\..\driver\hid2vsp\dist\hid2vsp.inf"
      File "..\..\driver\hid2vsp\dist\hid2vsp.dll"
      File "..\..\driver\hid2vsp\dist\hid2vsp.cat"
      ExecWait '"$SYSDIR\pnputil.exe" /add-driver "$INSTDIR\driver\hid2vsp.inf" /install'
  SectionEnd
  ```
- Default config now offers BOTH HTTP output and virtual COM output;
  user picks during install.

### HVCI smoke test
- Win 11 24H2 VM with HVCI on by default
- Install hid2serial setup.exe (driver included)
- After reboot, test pair survives + scans flow end-to-end

### Done gate
- Fresh Win 11 24H2 VM, HVCI confirmed on (`Get-CimInstance Win32_DeviceGuard | select SecurityServicesRunning`)
- `setup.exe` installs cleanly, no driver errors in Event Viewer
- Barcode scanner connected → daemon writes to COM31 → tester reads on COM32
- Uninstall removes driver + registry entries cleanly

## Out of scope for v1.0 (track in roadmap, not in this driver)

- TCP bridge (com2tcp clone) — separate userspace utility, not driver
- Parity / framing error injection — testing-only feature, defer
- RS-485 emulation (multidrop with explicit addressing) — niche
- Modem signaling beyond basic 4 (DTR/DSR/RTS/CTS) — sufficient for 99% of cases
- Per-pair flow-control modes beyond raw passthrough — adds complexity for marginal value
