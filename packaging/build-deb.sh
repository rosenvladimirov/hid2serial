#!/usr/bin/env bash
# Build a Debian package (.deb) for hid2serial.
#
# Why hand-rolled (no debhelper / dh_python3): the project is a single
# pure-Python module + a few config files; full debhelper machinery
# adds dependencies and complexity for no functional gain. We assemble
# a layout under build/deb-root/ and call `dpkg-deb --build` directly.
#
# Run from the repo root:
#   ./packaging/build-deb.sh
# Output: dist/hid2serial_<version>_all.deb
#
# Requires: dpkg-deb, fakeroot (both shipped in the `dpkg` and
# `fakeroot` packages on every Debian / Ubuntu).

set -euo pipefail

cd "$(dirname "$0")/.."

VERSION="$(python3 -c 'import tomllib; print(tomllib.loads(open("pyproject.toml").read())["project"]["version"])')"
ARCH="all"
PKG_NAME="hid2serial"
DEB="${PKG_NAME}_${VERSION}_${ARCH}.deb"

ROOT="build/deb-root"
rm -rf "$ROOT"
mkdir -p "$ROOT"

# ─── 1. Module installation under /usr/lib/python3/dist-packages ────
# We install via a regular Python `pip` so dependencies are recorded
# correctly. But we DON'T pip install dependencies inside the deb —
# they come from system packages declared in DEBIAN/control.
mkdir -p "$ROOT/usr/lib/python3/dist-packages"
cp -r hid2serial "$ROOT/usr/lib/python3/dist-packages/"
# Strip cache + build artefacts
find "$ROOT/usr/lib/python3/dist-packages/hid2serial" -name "__pycache__" -type d -exec rm -rf {} +
find "$ROOT/usr/lib/python3/dist-packages/hid2serial" -name "*.pyc" -delete

# ─── 2. Console-script entry point ──────────────────────────────────
mkdir -p "$ROOT/usr/bin"
cat > "$ROOT/usr/bin/hid2serial" <<'EOF'
#!/usr/bin/python3
import sys
from hid2serial.cli import app
sys.exit(app() or 0)
EOF
chmod 755 "$ROOT/usr/bin/hid2serial"

# ─── 3. Systemd service unit ────────────────────────────────────────
mkdir -p "$ROOT/lib/systemd/system"
cp service/hid2serial.service "$ROOT/lib/systemd/system/"

# ─── 4. polkit rule for passwordless toggle from tray ───────────────
mkdir -p "$ROOT/etc/polkit-1/rules.d"
cp service/polkit/40-hid2serial.rules "$ROOT/etc/polkit-1/rules.d/"

# ─── 5. Default config + udev rules (commented examples) ────────────
mkdir -p "$ROOT/etc/hid2serial"
cp config.example.yaml "$ROOT/etc/hid2serial/config.yaml.example"

mkdir -p "$ROOT/etc/udev/rules.d"
cp service/udev/99-hid2serial.rules "$ROOT/etc/udev/rules.d/"

# ─── 6. XDG desktop entry (tray autostart) ──────────────────────────
mkdir -p "$ROOT/etc/xdg/autostart"
cp service/desktop/hid2serial-tray.desktop "$ROOT/etc/xdg/autostart/"
mkdir -p "$ROOT/usr/share/applications"
cp service/desktop/hid2serial-tray.desktop "$ROOT/usr/share/applications/"

# ─── 7. Documentation ───────────────────────────────────────────────
mkdir -p "$ROOT/usr/share/doc/$PKG_NAME"
cp README.md LICENSE "$ROOT/usr/share/doc/$PKG_NAME/"
gzip -9 -f "$ROOT/usr/share/doc/$PKG_NAME/README.md"

# ─── 8. DEBIAN metadata ─────────────────────────────────────────────
mkdir -p "$ROOT/DEBIAN"
cat > "$ROOT/DEBIAN/control" <<EOF
Package: $PKG_NAME
Version: $VERSION
Section: misc
Priority: optional
Architecture: $ARCH
Maintainer: Rosen Vladimirov <vladimirov.rosen@gmail.com>
Depends: python3 (>= 3.11),
         python3-evdev,
         python3-yaml,
         python3-pydantic,
         python3-typer,
         python3-gi,
         gir1.2-ayatanaappindicator3-0.1 | gir1.2-appindicator3-0.1,
         gir1.2-gtk-3.0,
         policykit-1
Homepage: https://github.com/rosenvladimirov/hid2serial
Description: HID barcode reader to virtual serial port daemon
 hid2serial turns a USB / Bluetooth HID barcode scanner into a virtual
 serial port (pty on Linux). For Odoo POS, fiscal-printer and legacy
 POS integrations that expect a serial barcode device but the
 customer's hardware is a HID keyboard-wedge.
 .
 Includes a system-tray applet (Wayland and X11 compatible via Ayatana
 AppIndicator / StatusNotifierItem) that toggles the daemon on / off.
 When stopped, the scanner works as a plain HID keyboard; when
 started, it is grabbed exclusively and routed to the configured pty.
EOF

cat > "$ROOT/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e

# Reload systemd so it sees the new unit
if [ -d /run/systemd/system ]; then
    systemctl daemon-reload || true
fi

# Create the `hid2serial` group used by the polkit rule
if ! getent group hid2serial >/dev/null; then
    groupadd --system hid2serial
fi

# Auto-add the install user (the human running `sudo dpkg -i ...`) to
# the `hid2serial` group so the tray's Toggle action works without a
# password prompt. SUDO_USER is set by sudo; PKEXEC_UID by pkexec.
INSTALL_USER=""
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
    INSTALL_USER="$SUDO_USER"
elif [ -n "${PKEXEC_UID:-}" ] && [ "$PKEXEC_UID" != "0" ]; then
    INSTALL_USER="$(getent passwd "$PKEXEC_UID" | cut -d: -f1)"
fi
if [ -n "$INSTALL_USER" ]; then
    if ! id -nG "$INSTALL_USER" 2>/dev/null | grep -qw hid2serial; then
        usermod -aG hid2serial "$INSTALL_USER" || true
        echo "Added '$INSTALL_USER' to the 'hid2serial' group."
        echo "→ Re-login (or run 'newgrp hid2serial' in a shell) to activate."
    fi
fi

# Reload udev so the rules take effect
if command -v udevadm >/dev/null 2>&1; then
    udevadm control --reload || true
fi

# Auto-generate / refresh config.yaml.
#
# Bootstrap writes a GENERIC config (`match: { any_external: true }`)
# that grabs the first non-internal HID keyboard at runtime. Same code
# works for every scanner brand without per-device pinning.
#
# We overwrite the config in two cases:
#   1. It does not exist (clean install).
#   2. It looks like the previous example template — no user edits yet.
# We never overwrite a file that's been modified to point at a real
# scanner / multiple readers / non-default ttyV path.
SHOULD_REGEN=0
if [ ! -e /etc/hid2serial/config.yaml ]; then
    SHOULD_REGEN=1
elif grep -q "Honeywell Voyager 1450g" /etc/hid2serial/config.yaml \
     && grep -q "Datalogic QuickScan QD2430" /etc/hid2serial/config.yaml; then
    # Pristine example marker present → user hasn't customised yet
    SHOULD_REGEN=1
fi

if [ "$SHOULD_REGEN" = "1" ]; then
    /usr/bin/hid2serial bootstrap --force 2>/dev/null || true
    chmod 644 /etc/hid2serial/config.yaml
fi

cat <<NOTE

hid2serial is installed.

Service is NOT started automatically. Toggle from the tray icon
(auto-starts on next login, or run 'hid2serial tray' now), or:
  sudo systemctl start hid2serial      # one-off
  sudo systemctl enable --now hid2serial   # boot-persistent

Config:  /etc/hid2serial/config.yaml
Logs:    journalctl -u hid2serial.service -f
Doctor:  hid2serial doctor

NOTE
exit 0
EOF
chmod 755 "$ROOT/DEBIAN/postinst"

cat > "$ROOT/DEBIAN/prerm" <<'EOF'
#!/bin/sh
set -e
if [ -d /run/systemd/system ]; then
    systemctl stop hid2serial.service || true
    systemctl disable hid2serial.service || true
fi
exit 0
EOF
chmod 755 "$ROOT/DEBIAN/prerm"

cat > "$ROOT/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "purge" ]; then
    rm -f /etc/hid2serial/config.yaml
    rmdir /etc/hid2serial 2>/dev/null || true
    if getent group hid2serial >/dev/null; then
        groupdel hid2serial 2>/dev/null || true
    fi
fi
if [ -d /run/systemd/system ]; then
    systemctl daemon-reload || true
fi
exit 0
EOF
chmod 755 "$ROOT/DEBIAN/postrm"

# conffiles must reference paths that DO exist in the package. The
# active config (/etc/hid2serial/config.yaml) is created by postinst
# from the .example only if missing — so it's NOT shipped, hence not
# listed here. Users edit it freely; on upgrade we never overwrite.
cat > "$ROOT/DEBIAN/conffiles" <<EOF
/etc/hid2serial/config.yaml.example
/etc/polkit-1/rules.d/40-hid2serial.rules
/etc/udev/rules.d/99-hid2serial.rules
EOF

# ─── 9. Build ───────────────────────────────────────────────────────
mkdir -p dist
fakeroot dpkg-deb --build --root-owner-group "$ROOT" "dist/$DEB"

echo
echo "Built: dist/$DEB"
ls -lh "dist/$DEB"
echo
echo "Quick install on this machine:"
echo "  sudo dpkg -i dist/$DEB"
echo "  sudo apt-get install -f          # if any deps are missing"
