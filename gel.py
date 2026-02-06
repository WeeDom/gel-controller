#! /usr/bin/env python3

import signal
import sys
import logging
from time import sleep
from gel_controller import Room, RoomController

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

"""Room management for GEL Controller."""
print("I'll be your Maggie, darling.\n")

## one room to rule them all
room_controller = RoomController()

# Create and configure room
room = Room(room_id="101", name="Conference Room", initial_state="empty")
print(f"Created room: {room.name} with ID: {room.room_id}")
room_controller.add_room(room)

# Discover devices
print(f"\n🔍 Discovering devices...")
sensors = room.get_person_detectors()
print(f"✓ Presence sensors: {len(sensors)}")
for sensor in sensors:
    print(f"  - {sensor.name} @ {sensor.ip}:{sensor.port}")

cameras = room.get_cameras()
print(f"✓ Cameras: {len(cameras)}")
for camera in cameras:
    print(f"  - {camera.name} (MAC: {camera.mac}) @ {camera.ip}:{camera._port}")

# Setup signal handler for graceful shutdown
def signal_handler(sig, frame):
    print("\n\n🛑 Shutting down Guard-e-loo...")
    room_controller.shutdown()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# Start the monitoring system
print(f"\n🚀 Starting Guard-e-loo monitoring system...")
room_controller.start()
print(f"✓ System running. Press Ctrl+C to stop.\n")

# Keep running
try:
    while room_controller.is_running():
        sleep(1)
except KeyboardInterrupt:
    pass
finally:
    room_controller.shutdown()
    print("👋 Goodbye!")