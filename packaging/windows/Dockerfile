# Build container for the hid2serial Windows installer.
#
# Usage from the repo root (no NSIS / curl / unzip needed on host):
#   ./packaging/windows/build-installer.sh
#
# The script runs this image, mounts the repo at /src, and produces
# dist/hid2serial-<version>-setup.exe on the host.
#
# Why Ubuntu 24.04 base: ships NSIS 3.x, modern Python 3.12, and
# avoids the older "nsis-pluginapi-cross" missing in slim images.

FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update -qq \
    && apt-get install -y --no-install-recommends \
        nsis \
        nsis-pluginapi \
        curl \
        unzip \
        python3 \
        python3-pip \
        python3-pil \
        ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# We can't run the embedded Windows Python from inside Linux, but
# `pip download --platform win_amd64` lets us stage all wheels.
# Also: pip is needed in the build container to ORCHESTRATE the
# wheel download.

WORKDIR /src
ENTRYPOINT ["bash", "/src/packaging/windows/build-installer.sh"]
