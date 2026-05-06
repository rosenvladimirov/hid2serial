#!/usr/bin/env bash
#
# Build the hid2serial Windows installer (.exe) inside a Docker
# container. Host needs ONLY Docker — no NSIS / curl / unzip / Python
# build deps required.
#
# Usage from repo root:
#   ./packaging/windows/build-installer.sh
#
# Output: dist/hid2serial-<version>-setup.exe
#
# Build container is `hid2serial-winbuild:latest` (built once, cached
# afterwards). Re-run is fast — only the wheel-download + makensis
# steps run every time.

set -euo pipefail

cd "$(dirname "$0")/../.."   # repo root

IMAGE="hid2serial-winbuild:latest"
DOCKERFILE="packaging/windows/Dockerfile"

# ─── Detect mode: orchestrator (host) vs runner (container) ─

if [[ "${IN_CONTAINER:-0}" != "1" ]]; then
    # ===== HOST PATH — orchestrate Docker =====
    if ! command -v docker >/dev/null 2>&1; then
        echo "ERROR: Docker is required on the host."
        echo "  Install: https://docs.docker.com/engine/install/"
        exit 1
    fi

    echo "→ Building / refreshing the build image ($IMAGE)..."
    docker build -q -f "$DOCKERFILE" -t "$IMAGE" packaging/windows/ >/dev/null

    echo "→ Running build inside container..."
    docker run --rm \
        -v "$PWD:/src" \
        -e IN_CONTAINER=1 \
        "$IMAGE"
    exit 0
fi

# ===== CONTAINER PATH — actual build =====

VERSION="$(python3 -c 'import tomllib; print(tomllib.loads(open("/src/pyproject.toml").read())["project"]["version"])')"
PYTHON_VER="3.12.7"
ARCH="amd64"
PY_EMBED_URL="https://www.python.org/ftp/python/${PYTHON_VER}/python-${PYTHON_VER}-embed-${ARCH}.zip"

BUILD="/src/build/win"
PY_DIR="$BUILD/python"

echo "=== hid2serial Windows installer build (v${VERSION}) ==="

# 1. Clean + create build dir
rm -rf "$BUILD"
mkdir -p "$PY_DIR"

# 2. Download Python embeddable distribution
echo "→ Downloading Python ${PYTHON_VER} embeddable (${ARCH})..."
curl -fsSL "$PY_EMBED_URL" -o "$BUILD/python-embed.zip"
unzip -q "$BUILD/python-embed.zip" -d "$PY_DIR"

# Embedded Python disables site-packages by default. Uncomment
# `import site` in pythonNN._pth so our installed wheels are
# importable on Windows.
PTH_FILE="$(ls "$PY_DIR"/python*._pth 2>/dev/null | head -1)"
if [[ -n "$PTH_FILE" ]]; then
    sed -i 's/^#import site/import site/' "$PTH_FILE"
fi

# 3. Download Windows-compatible wheels
WHEEL_DIR="$BUILD/wheels"
mkdir -p "$WHEEL_DIR"

PIP_DEPS=(
    "pyyaml>=6.0"
    "pydantic>=2.5"
    "typer>=0.12"
    "pywin32>=306"
    "pyserial>=3.5"
    "pystray>=0.19"
    "pillow>=10"
)

echo "→ Downloading Windows wheels..."
python3 -m pip download \
    --platform win_amd64 \
    --python-version 312 \
    --only-binary=:all: \
    --dest "$WHEEL_DIR" \
    "${PIP_DEPS[@]}" 2>&1 | tail -5

# 4. Unpack wheels into embedded Python's Lib/site-packages
mkdir -p "$PY_DIR/Lib/site-packages"
echo "→ Unpacking wheels..."
for whl in "$WHEEL_DIR"/*.whl; do
    [[ -e "$whl" ]] || continue
    unzip -qo "$whl" -d "$PY_DIR/Lib/site-packages"
done

# 5. Copy hid2serial source
mkdir -p "$PY_DIR/Lib/site-packages/hid2serial"
cp /src/hid2serial/*.py "$PY_DIR/Lib/site-packages/hid2serial/"

# 6. Generate placeholder icon if absent (PIL is in the image)
mkdir -p /src/packaging/windows/icons
if [[ ! -e /src/packaging/windows/icons/hid2serial.ico ]]; then
    python3 - <<'PYEOF'
from PIL import Image, ImageDraw
img = Image.new("RGBA", (256, 256), (16, 185, 129, 255))
d = ImageDraw.Draw(img)
# Crude HID-keyboard glyph
d.rounded_rectangle((40, 90, 216, 200), radius=12, fill=(255, 255, 255))
for col in range(56, 200, 24):
    d.rounded_rectangle((col, 110, col+16, 130), radius=2, fill=(16, 185, 129))
    d.rounded_rectangle((col, 150, col+16, 170), radius=2, fill=(16, 185, 129))
img.save("/src/packaging/windows/icons/hid2serial.ico",
         format="ICO",
         sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])
print("Generated placeholder icon.")
PYEOF
fi

# 7. Stage hid2serial dir for the NSIS File /r directive (separate
#    from the Python embedded tree so it ends up at $INSTDIR\hid2serial\)
mkdir -p "$BUILD/hid2serial"
cp -r /src/hid2serial/. "$BUILD/hid2serial/"

# 8. Run makensis
mkdir -p /src/dist
echo "→ Running makensis (NSIS 3.x)..."
cd /src/packaging/windows
makensis -V2 \
    -DAPP_VERSION="$VERSION" \
    installer.nsi

# 9. Report
SETUP="/src/dist/hid2serial-${VERSION}-setup.exe"
if [[ -e "$SETUP" ]]; then
    echo
    echo "=========================================================="
    echo "  Built: dist/hid2serial-${VERSION}-setup.exe"
    ls -lh "$SETUP"
    echo "=========================================================="
    echo
    echo "Test on Windows:"
    echo "  - Right-click setup.exe → Run as administrator"
    echo "  - Follow the wizard"
    echo "  - Edit %PROGRAMDATA%\\hid2serial\\config.yaml"
    echo "  - Tray icon appears on next login (or 'hid2serial tray' in Run dialog)"
    echo "  - 'sc start hid2serial' starts the redirect daemon"
else
    echo "ERROR: setup.exe not produced."
    exit 1
fi
