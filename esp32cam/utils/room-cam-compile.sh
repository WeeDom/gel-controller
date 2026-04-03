#! /usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKETCH_DIR="${SCRIPT_DIR}/../CameraWebServer"
BUILD_DIR="${SCRIPT_DIR}/../.arduino-build/esp32cam"

arduino-cli compile \
	--fqbn esp32:esp32:esp32cam \
	--board-options PartitionScheme=min_spiffs \
	--output-dir "${BUILD_DIR}" \
	"${SKETCH_DIR}"
