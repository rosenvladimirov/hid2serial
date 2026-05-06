# hid2serial

> Daemon that turns a USB HID barcode scanner into a virtual serial port.

For Odoo POS, fiscal-printer, and legacy POS integrations that expect a
serial barcode device, but the customer's hardware is a cheap USB
"keyboard wedge" scanner. `hid2serial` runs on the host (or any PC the
scanner is plugged into), grabs the scanner exclusively so its keys
don't bleed into the focused window, and re-emits each scan on a
virtual serial port (`/dev/ttyV0` on Linux, COMxx on Windows).

The Bulgarian retail integration we ship this with (`Odoo.ErpNet.FP`)
runs the proxy in Docker. The Docker container reads the pty as a
plain serial device — no special HID support is required inside the
container. `hid2serial` lives on the host; the proxy reacts.

## Architecture

```
┌──────────────┐  evdev grab()    ┌─────────────────┐  pty (/dev/ttyV0)  ┌─────────────────┐
│ HID Scanner  │ ────────────────▶│  hid2serial     │ ─────────────────▶ │  Consumer       │
│ (USB kbd)    │                  │  daemon (host)  │                    │  ErpNet.FP /    │
└──────────────┘                  │  - keymap (US)  │                    │  Odoo POS /     │
                                  │  - line buffer  │                    │  fiscal driver  │
                                  │  - terminator   │                    └─────────────────┘
                                  └─────────────────┘
```

On Linux the consumer runs in any namespace (host, Docker container with
`/dev` mount, anywhere with read permission on the symlink target). The
pty kernel driver doesn't care; `pyserial` opens it like any UART.

## Status

**v0.1 — Linux only.** Windows backend is in the design (RawInput +
com0com) but not yet implemented; PRs welcome.

## Installation

```bash
# As root:
pip install -e .[linux]              # or pip install hid2serial[linux] when published

# Optional — if you want non-root operation:
groupadd -f input                    # usually already exists
usermod -aG input <your-service-user>
cp service/udev/99-hid2serial.rules /etc/udev/rules.d/
udevadm control --reload && udevadm trigger

# systemd:
cp service/hid2serial.service /etc/systemd/system/
mkdir -p /etc/hid2serial
cp config.example.yaml /etc/hid2serial/config.yaml
systemctl daemon-reload
systemctl enable --now hid2serial
```

## Configuration

Edit `/etc/hid2serial/config.yaml`. Minimal example:

```yaml
readers:
  - name: pos1
    match: { vid: 0x0c2e, name_regex: "Honeywell|Voyager" }
    output:
      linux: { symlink: /dev/ttyV0, permissions: "0666" }
    framing:
      terminator: "\r\n"
```

The full schema lives in [`config.example.yaml`](config.example.yaml).

## CLI

```bash
hid2serial list-readers       # what HID-keyboard devices does the kernel see?
hid2serial doctor             # are evdev / udev / systemd ready?
hid2serial run -c /etc/hid2serial/config.yaml
hid2serial test --reader pos1 # capture one scan and print to stdout
```

## Wiring with `Odoo.ErpNet.FP`

Once the daemon is up and `/dev/ttyV0` exists, point the proxy at it as
a plain serial reader:

```yaml
# config.yaml of Odoo.ErpNet.FP
readers:
  - id: hw1
    transport: serial
    port: /dev/ttyV0
    baudrate: 9600
    encoding: ascii
    webhooks:
      - https://erp.example.com/erp_net_fp/reader/scan
```

That's the whole integration. The Docker container with `-v /dev:/dev`
sees the pty just like any other serial device.

## The three traps to avoid

1. **Scancode leakage** — without the exclusive `grab()` every scan
   also types into whatever window has focus. We always grab on Linux;
   the Windows backend will use a per-device RawInput filter.

2. **Keyboard layout corruption** — many BG installations have the
   system keymap set to bg-cyrillic. A scanner programmed as US QWERTY
   emitting `1234` may render as Cyrillic letters depending on focus.
   `hid2serial` translates scancodes itself with a hardcoded US keymap,
   ignoring the system layout entirely.

3. **Buffering & framing** — readers send keystrokes one at a time at
   high speed. The daemon buffers until the configured terminator key,
   then flushes the full barcode to the serial side as one atomic
   write. Without buffering, consumers see partial reads.

## License

LGPL-3.0-or-later. See [LICENSE](LICENSE).

---

*Authored by Rosen Vladimirov · Assisted by Claude Code*
