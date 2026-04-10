#!/usr/bin/env python3
"""Door camera deploy wrapper.

This keeps a dedicated entrypoint while delegating all logic to deploy_esp32_camera.py.
"""

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
BASE_SCRIPT = ROOT / "deploy_esp32_camera.py"


def main() -> int:
    cmd = [
        sys.executable,
        str(BASE_SCRIPT),
        "--target",
        "door",
        "--sketch",
        "esp32cam-door/DoorCamera",
        "--build-dir",
        ".arduino-build/esp32cam-door",
        "--config-mode",
        "none",
        *sys.argv[1:],
    ]
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
