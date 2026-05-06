# Setup checklist — Win 11 dev VM for hid2vsp

Run through this once when you create the VM. After this is done you
can hand the README + dev_plan to a Claude session inside the VM and it
has everything to start coding.

## VM specs (any hypervisor — Hyper-V, VirtualBox, KVM, VMware)

- **OS:** Windows 11 Pro, build 26100 (24H2) or newer — fresh install
- **CPU:** ≥4 cores
- **RAM:** ≥8 GB (16 GB more comfortable when VS is open)
- **Disk:** ≥80 GB (VS + WDK + samples = ~30 GB; rest for builds and snapshots)
- **Networking:** NAT or bridged — needs internet for VS / WDK install + GitHub + cloud Claude
- **Secure Boot:** ON during install (gives you HVCI for realistic testing later)
- **TPM:** virtual TPM 2.0 enabled — required by Win 11

## Snapshot baseline

Take a VM snapshot **immediately after install + activation, before any
dev tools**. Lets you revert if WDK or VS install corrupts something.

## Software install (in this order)

1. **Windows Updates** — fully patched, reboot until clean
2. **Git for Windows** — https://git-scm.com/download/win — accept defaults
3. **Visual Studio 2022 Community** — https://visualstudio.microsoft.com/downloads/
   - Workloads: **Desktop development with C++**
   - Individual components: **Windows 11 SDK (latest)**, **MSVC v143 spectre-mitigated libs (Latest, x64/x86)**, **Windows Driver Kit (WDK)**
   - ~15 GB download, takes 30-60 minutes
4. **WDK 10** — if not picked up by VS Installer, install separately from
   https://learn.microsoft.com/windows-hardware/drivers/download-the-wdk
   - Installer integrates with VS — open VS → File → New → Project → search "Driver" → templates appear
5. **Python 3.12** — https://www.python.org/downloads/ — check "Add to PATH"
6. **pyserial** — `pip install pyserial` (for round-trip tests)
7. **Optional:** PuTTY for manual COM port testing — https://putty.org/

## One-time security configuration

Open elevated cmd, run:

```cmd
:: Enable test signing — required to load self-signed driver during dev
bcdedit /set testsigning on

:: Disable HVCI temporarily for Phase 1-3 dev (re-enable for Phase 4 prod test)
:: Settings → Privacy & Security → Windows Security → Device Security
::         → Core Isolation → Memory Integrity → Off

:: Reboot
shutdown /r /t 0
```

After reboot, verify in lower-right of desktop you see "Test Mode"
watermark — confirms test-signed drivers will load.

## Generate self-signed dev cert

```cmd
:: Open "Developer Command Prompt for VS 2022" (has makecert.exe in PATH)
cd C:\Users\<you>\
makecert -r -pe -ss MyCertStore -n "CN=hid2vsp Dev" hid2vsp-dev.cer
certmgr.exe -add hid2vsp-dev.cer -s -r localMachine root
certmgr.exe -add hid2vsp-dev.cer -s -r localMachine trustedpublisher
```

## Clone repos

```cmd
mkdir C:\src
cd C:\src
git clone https://github.com/microsoft/Windows-driver-samples.git winsamples
git clone https://github.com/rosenvladimirov/hid2serial.git hid2serial
```

The MS samples repo gives us the `serial/VirtualSerial2` starting point.
The `hid2serial` repo holds our own work under `driver/hid2vsp/`.

## Sanity check — build the unmodified MS sample

```cmd
cd C:\src\winsamples\serial\VirtualSerial2
:: Open VirtualSerial2.sln in VS 2022
:: Build → Build Solution (Debug | x64)
:: Output should be in x64\Debug\VirtualSerial2\
```

If this build fails, fix the toolchain BEFORE copying anything to
`hid2serial`. Common fixes: install missing WDK component, install
spectre-mitigated libs, target Win 11 SDK 10.0.26100.0.

## Hand off to Claude inside the VM

Once the sanity-check build succeeds:

```cmd
cd C:\src\hid2serial
claude
```

Then in the Claude session:

> Read `driver/hid2vsp/README.md` and `driver/hid2vsp/docs/dev_plan.md`.
> Start Phase 1 — fork the MS VirtualSerial2 sample into
> `driver/hid2vsp/src/`, rename to hid2vsp, build, install, verify
> COM port appears in Device Manager and is openable from pyserial.
> Stop at the Phase 1 done-gate — do not proceed to Phase 2 without
> manual sign-off.

That's the whole bootstrap.
