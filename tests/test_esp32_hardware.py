"""
Test suite for ESP32 camera property endpoints on real hardware.

Tests compile C++ code, upload to device, and verify endpoints work correctly.

Usage:
    export ESP32_CAMERA_IP=192.168.1.100
    export ESP32_UPLOAD_PORT=/dev/ttyUSB0  # optional
    pytest tests/test_esp32_hardware.py -v
"""

import pytest
import subprocess
import time
import requests
import os
from pathlib import Path


@pytest.fixture(scope="module")
def esp32_device():
    """
    Setup ESP32 device for testing.

    Compiles and uploads the current code to the device,
    then provides the device URL for testing.
    """
    esp32_ip = os.environ.get("ESP32_CAMERA_IP")
    upload_port = os.environ.get("ESP32_UPLOAD_PORT", "")

    if not esp32_ip:
        pytest.skip("ESP32_CAMERA_IP environment variable not set. Set it to run hardware tests.")

    sketch_path = Path(__file__).parent.parent / "esp32cam" / "CameraWebServer"
    fqbn = "esp32:esp32:esp32cam"

    print(f"\n{'='*60}")
    print(f"ESP32 Camera Hardware Tests")
    print(f"{'='*60}")
    print(f"Device IP: {esp32_ip}")
    print(f"Sketch: {sketch_path}")

    # Step 1: Compile and upload code
    print(f"\n[1/4] Compiling and uploading to ESP32...")
    try:
        cmd = ["arduino-cli", "compile",
               "--upload", str(sketch_path),
               "--fqbn", fqbn]
        if upload_port:
            cmd.extend(["-p", upload_port])
        else:
            # Try to use the port from preferences if available
            cmd.extend(["-p", "/dev/ttyUSB0"])

        compile_result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120
        )

        if compile_result.returncode != 0:
            print(f"Compile/Upload output:\n{compile_result.stdout}")
            print(f"Errors:\n{compile_result.stderr}")
            pytest.skip(f"Failed to compile/upload sketch")

        print("✓ Code uploaded successfully")

    except FileNotFoundError:
        pytest.skip("arduino-cli not found. Install it to run hardware tests.")
    except subprocess.TimeoutExpired:
        pytest.skip("Compile/upload timeout after 120s")

    # Step 2: Wait for device to boot and connect
    print(f"\n[2/4] Waiting for device to boot...")
    time.sleep(5)  # Give ESP32 time to restart and connect to WiFi

    # Step 3: Verify device is reachable
    print(f"[3/4] Verifying device connectivity...")
    base_url = f"http://{esp32_ip}"
    max_retries = 10

    for attempt in range(max_retries):
        try:
            response = requests.get(f"{base_url}/props", timeout=3)
            response.raise_for_status()
            print(f"✓ Device ready at {base_url}")
            print(f"{'='*60}\n")
            break
        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                time.sleep(2)
            else:
                pytest.skip(f"ESP32 not reachable at {esp32_ip} after upload: {e}")

    yield base_url

    print(f"\n{'='*60}")
    print("Test suite completed")
    print(f"{'='*60}")


class TestCameraPropsGet:
    """Test GET /props endpoint."""

    def test_get_props_returns_json(self, esp32_device):
        """Should return camera properties as JSON."""
        response = requests.get(f"{esp32_device}/props")

        assert response.status_code == 200
        assert "application/json" in response.headers.get("Content-Type", "")

        data = response.json()
        assert "name" in data
        assert "room_id" in data
        assert "poll_interval" in data

    def test_get_props_has_cors_header(self, esp32_device):
        """Should include CORS headers."""
        response = requests.get(f"{esp32_device}/props")

        assert response.headers.get("Access-Control-Allow-Origin") == "*"


class TestCameraPropsSet:
    """Test POST /props endpoint."""

    def test_set_all_props(self, esp32_device):
        """Should update all camera properties."""
        new_props = {
            "name": "test_camera",
            "room_id": "test_room",
            "poll_interval": 5.5
        }

        response = requests.post(
            f"{esp32_device}/props",
            json=new_props,
            headers={"Content-Type": "application/json"}
        )

        assert response.status_code == 200
        assert response.text == "ok"

        # Verify properties were updated
        get_response = requests.get(f"{esp32_device}/props")
        data = get_response.json()

        assert data["name"] == "test_camera"
        assert data["room_id"] == "test_room"
        assert data["poll_interval"] == 5.5

    def test_set_props_with_float_interval(self, esp32_device):
        """Should accept float values for poll_interval."""
        response = requests.post(
            f"{esp32_device}/props",
            json={"name": "cam", "room_id": "room", "poll_interval": 15.7}
        )

        assert response.status_code == 200

        data = requests.get(f"{esp32_device}/props").json()
        assert data["poll_interval"] == 15.7

    def test_set_props_has_cors_header(self, esp32_device):
        """POST should include CORS headers."""
        response = requests.post(
            f"{esp32_device}/props",
            json={"name": "cam", "room_id": "room", "poll_interval": 10.0}
        )

        assert response.headers.get("Access-Control-Allow-Origin") == "*"


class TestCameraPropsIntegration:
    """Integration tests for property management."""

    def test_roundtrip_props(self, esp32_device):
        """Should persist properties across GET/POST cycles."""
        original = {
            "name": "front_door",
            "room_id": "entrance",
            "poll_interval": 3.14
        }

        # Set properties
        requests.post(f"{esp32_device}/props", json=original)

        # Get properties
        response = requests.get(f"{esp32_device}/props")
        data = response.json()

        assert data["name"] == original["name"]
        assert data["room_id"] == original["room_id"]
        # Float comparison with tolerance
        assert abs(data["poll_interval"] - original["poll_interval"]) < 0.01

    def test_long_strings_truncated(self, esp32_device):
        """Should truncate strings longer than 31 characters."""
        long_name = "a" * 50  # 50 characters

        requests.post(
            f"{esp32_device}/props",
            json={"name": long_name, "room_id": "room", "poll_interval": 10.0}
        )

        data = requests.get(f"{esp32_device}/props").json()

        # Should be truncated to 31 chars
        assert len(data["name"]) <= 31
