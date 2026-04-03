# ESP32 Camera Testing Guide

This directory contains tools for testing ESP32 camera property endpoints without constant re-flashing.

## Quick Start

### Test with Real Hardware

```bash
# Set your ESP32's IP address
export ESP32_CAMERA_IP=192.168.1.100

# Run tests against real device
source venv/bin/activate
pytest tests/test_esp32_camera_props.py -v
```

### Test with Mock (No Hardware)

```bash
# Don't set ESP32_CAMERA_IP
pytest tests/test_esp32_camera_props.py -v
```

## Testing Workflow

1. **Development Phase** - Use mock server for rapid iteration
   - No hardware needed
   - Instant feedback
   - Good for Python controller development

2. **Integration Phase** - Test against real ESP32
   - Verifies actual C++ code
   - Catches serialization issues
   - Confirms hardware behavior

3. **CI/CD** - Run both
   - Mock tests run on every commit
   - Hardware tests run before deployment (if device available)

## Manual Testing

### Using Mock Server

```bash
# Terminal 1: Start mock server
python esp32cam/mock_camera_server.py 8080

# Terminal 2: Test with curl
curl http://localhost:8080/props

curl -X POST http://localhost:8080/props \
  -H 'Content-Type: application/json' \
   -d '{"name":"test_cam","room_id":"bedroom","location":"upstairs","poll_interval":5.0}'
```

### Using Real Hardware

```bash
# Get current properties
curl http://192.168.1.100/props

# Update properties
curl -X POST http://192.168.1.100/props \
  -H 'Content-Type: application/json' \
   -d '{"name":"front_door","room_id":"entrance","location":"front-porch","poll_interval":15.0}'
```

## Adding New Properties

When you add a new property to the C++ code:

1. Update [app_httpd.cpp](CameraWebServer/app_httpd.cpp):
   - Add variable declaration (e.g., `static bool new_prop = false;`)
   - Update `props_get_handler` JSON response
   - Update `props_set_handler` parsing logic

2. Update mock server [mock_camera_server.py](mock_camera_server.py):
   - Add class variable
   - Update GET response
   - Update POST parsing

3. Update Python controller [../gel_controller/camera.py](../gel_controller/camera.py):
   - Add getter/setter methods
   - Update initialization

4. Update tests [../tests/test_esp32_camera_props.py](../tests/test_esp32_camera_props.py):
   - Add test cases for new property

5. **Run tests against real hardware** to verify:
   ```bash
   export ESP32_CAMERA_IP=<your-ip>
   pytest tests/test_esp32_camera_props.py -v
   ```

## Test Coverage

Current tests verify:
- ✅ GET /props returns all properties
- ✅ POST /props updates properties
- ✅ CORS headers present
- ✅ Error handling (empty body, invalid JSON)
- ✅ String truncation (31 char limit)
- ✅ Float precision for poll_interval
- ✅ Property persistence across requests

## Troubleshooting

**ESP32 not reachable:**
- Check device is powered and connected to network
- Verify IP address: `ping <ESP32_IP>`
- Check firewall settings
- Ensure camera web server is running

**Tests fail on real hardware but pass with mock:**
- This indicates a real bug in C++ code
- Check ESP32 serial output for errors
- Verify JSON format matches exactly
- Check for buffer overflows or parsing issues

**Mock server conflicts:**
- If port 8765 is busy, kill process: `lsof -ti:8765 | xargs kill -9`
- Or change port in test file
