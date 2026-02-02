#!/usr/bin/env python3
"""
Unified device discovery for the gel-controller system.
Discovers both cameras and PIR sensors on the network.
"""

from detect_camera import discover_cameras
from gel_controller.devices.pir import discover_presence_sensors
import json


def discover_all_devices():
    """Discover all cameras and PIR sensors"""
    print("🔍 GEL-CONTROLLER DEVICE DISCOVERY")
    print("="*60)

    # Discover cameras
    print("\n📷 CAMERAS")
    print("-"*60)
    cameras = discover_cameras()

    # Discover PIR sensors
    print("\n🚶 PRESENCE SENSORS")
    print("-"*60)
    sensors = discover_presence_sensors()

    # Summary
    print("\n" + "="*60)
    print("DISCOVERY SUMMARY")
    print("="*60)
    print(f"Cameras found: {len(cameras)}")
    print(f"PIR sensors found: {len(sensors)}")

    return {
        "cameras": cameras,
        "sensors": sensors
    }


if __name__ == "__main__":
    devices = discover_all_devices()

    print("\n" + "="*60)
    print("FULL DEVICE LIST (JSON)")
    print("="*60)
    print(json.dumps(devices, indent=2))
