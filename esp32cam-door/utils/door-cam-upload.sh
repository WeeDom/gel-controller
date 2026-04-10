#! /usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKETCH_DIR="${SCRIPT_DIR}/../DoorCamera"
BUILD_DIR="${SCRIPT_DIR}/../.arduino-build/esp32cam-door"
PORT="${1:-/dev/ttyUSB0}"

arduino-cli upload \
  --fqbn esp32:esp32:esp32cam \
  --input-dir "${BUILD_DIR}" \
  -p "${PORT}" \
  "${SKETCH_DIR}"
