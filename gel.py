#! /usr/bin/env python3

import os
import signal
import sys
import logging
from time import sleep
from datetime import datetime
from pathlib import Path


def _maybe_reexec_with_venv_python() -> None:
    """When run via sudo, prefer project venv Python so dependencies resolve."""
    script_path = Path(__file__).resolve()
    project_root = script_path.parent
    venv_python = project_root / "venv" / "bin" / "python"

    if os.geteuid() != 0:
        return
    if not venv_python.exists():
        return

    current_python = Path(sys.executable).resolve()
    if current_python == venv_python.resolve():
        return

    os.execv(str(venv_python), [str(venv_python), str(script_path), *sys.argv[1:]])


_maybe_reexec_with_venv_python()

from gel_controller import Room, RoomController

# Create logs directory
log_dir = Path("logs")
log_dir.mkdir(exist_ok=True)

# Generate log filename with timestamp
log_file = log_dir / f"gel-{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

# Configure logging to both file and console
logging.basicConfig(
    level=logging.INFO,  # Capture everything
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

"""Room management for GEL Controller."""
print("Maggie reporting for duty.\n")
logger.info(f"📝 Logging to: {log_file}")

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