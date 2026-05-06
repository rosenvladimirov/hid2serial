"""
Cross-platform tray entry point — routes to the right backend per OS.

Linux:  PyGObject + AyatanaAppIndicator (Wayland + X11 via SNI/D-Bus)
Windows: pystray (deferred — wired when Windows backend lands)
macOS:  pystray (deferred)
"""

from __future__ import annotations

import sys


def main() -> int:
    if sys.platform.startswith("linux"):
        from . import tray_linux
        return tray_linux.main()
    if sys.platform == "win32":
        try:
            from . import tray_windows
            return tray_windows.main()
        except ImportError:
            print(
                "Tray for Windows not yet available — Windows backend "
                "(RawInput + com0com) is on the v0.2 roadmap.",
                file=sys.stderr,
            )
            return 2
    if sys.platform == "darwin":
        print("macOS tray not yet implemented (deferred to v0.3+).",
              file=sys.stderr)
        return 2
    print(f"Unsupported platform: {sys.platform}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
