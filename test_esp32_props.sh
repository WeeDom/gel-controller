#!/bin/bash
# Test runner for ESP32 camera property endpoints

set -e

echo "================================"
echo "ESP32 Camera Props Test Suite"
echo "================================"
echo ""

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}Running tests for camera property endpoints${NC}"
echo ""

# Run pytest on the ESP32 camera tests
python -m pytest tests/test_esp32_camera_props.py -v

echo ""
echo -e "${GREEN}✓ All tests passed!${NC}"
echo ""
echo "You can also run the mock server manually:"
echo "  python esp32cam/mock_camera_server.py 8080"
echo ""
echo "Then test with curl:"
echo "  curl http://localhost:8080/props"
echo "  curl -X POST http://localhost:8080/props -H 'Content-Type: application/json' -d '{\"name\":\"cam1\",\"room_id\":\"living_room\",\"poll_interval\":5.0}'"
