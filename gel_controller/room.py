"""
Room - Represents a physical room with state management.
"""

import logging
import threading
import time
from datetime import datetime
from typing import List, Optional
from .devices.pir import discover_presence_sensors
from .devices.camera import discover_cameras
from .person_detector import PersonDetector
from .camera import Camera

logger = logging.getLogger(__name__)


class Room:
    """
    Represents a physical room with occupancy state.

    Acts as central state holder that:
    - PersonDetectors write to (update state)
    - Cameras read from (check if they can activate)
    """

    def __init__(self, room_id: str, name: str, initial_state: str = "empty"):
        """
        Initialize a Room.

        Args:
            room_id: Unique identifier for the room
            name: Human-readable room name
            initial_state: Initial room state (default: "empty")

        Raises:
            ValueError: If initial_state is invalid
        """
        self._room_id = room_id
        self._name = name
        self._state = None
        self._cameras: List['Camera'] = []
        self._person_detectors: List['PersonDetector'] = []

        # Capture timer management
        self._empty_timer: Optional[threading.Timer] = None
        self._capture_delay = 30.0  # 30 seconds in seconds

        # Validate and set initial state
        self.state = initial_state

    @property
    def room_id(self) -> str:
        """Get room ID."""
        return self._room_id


    @room_id.setter
    def room_id(self, id: str) -> None:
        """Set room ID."""
        self._room_id = id

    def get_room_id(self) -> str:
        """Backward-compatible getter for room ID."""
        return self.room_id

    def set_room_id(self, room_id: str) -> None:
        """Backward-compatible setter for room ID."""
        self.room_id = room_id


    @property
    def name(self) -> str:
        """Get room name."""
        return self._name

    @name.setter
    def name(self, name: str) -> None:
        """Set room name."""
        self._name = name

    def get_name(self) -> str:
        """Backward-compatible getter for room name."""
        return self.name

    def set_name(self, name: str) -> None:
        """Backward-compatible setter for room name."""
        self.name = name

    @property
    def state(self) -> Optional[str]:
        """Get current room state."""
        return self._state

    def get_state(self) -> Optional[str]:
        """Backward-compatible getter for room state."""
        return self.state

    def set_state(self, state: str) -> None:
        """Backward-compatible setter for room state."""
        self.state = state

    @state.setter
    def state(self, state: str) -> None:
        """
        Set room state.

        Args:
            state: New state ("occupied" or "empty")

        Raises:
            ValueError: If state is invalid
        """
        valid_states = ["occupied", "empty"]
        if state not in valid_states:
            raise ValueError(f"Invalid state: {state}. Must be one of {valid_states}")

        old_state = self._state
        self._state = state  # Actually update the state!

        if old_state != state:
            logger.info(f"Room {self._name} state changed: {old_state} → {state}")

            # Handle state transitions
            if state == "empty" and old_state == "occupied":
                self._start_empty_timer()
            elif state == "occupied" and old_state == "empty":
                self._cancel_empty_timer()

    def _start_empty_timer(self) -> None:
        """Start 3-minute timer when room becomes empty."""
        self._cancel_empty_timer()  # Cancel any existing timer

        logger.info(f"🕐 Room {self._name}: Starting {self._capture_delay/60:.1f}-minute capture timer")
        self._empty_timer = threading.Timer(self._capture_delay, self._trigger_capture)
        self._empty_timer.daemon = True
        self._empty_timer.start()
        logger.info(f"✓ Timer started, will capture at {time.strftime('%H:%M:%S', time.localtime(time.time() + self._capture_delay))}")


    def _cancel_empty_timer(self) -> None:
        """Cancel the empty timer if it exists."""
        if self._empty_timer is not None:
            self._empty_timer.cancel()
            self._empty_timer = None
            logger.info(f"⏹️  Room {self._name}: Cancelled capture timer (room occupied again)")

    def _trigger_capture(self) -> None:
        """Trigger all cameras to capture images after 3 minutes of empty room."""
        logger.info(f"Room {self._name}: 3 minutes elapsed, triggering camera captures")
        import requests
        from pathlib import Path

        # Create captures directory if it doesn't exist
        capture_dir = Path("captures")
        capture_dir.mkdir(exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        for camera in self._cameras:
            try:
                capture_url = f"http://{camera.ip}/capture"
                logger.info(f"Capturing from {camera.name} at {capture_url}")

                response = requests.get(
                    capture_url,
                    timeout=10,
                    headers={
                        'User-Agent': 'GEL-Controller/1.0'
                    }
                )

                if response.status_code == 200:
                    filename = capture_dir / f"{camera.name}-{timestamp}.jpeg"
                    filename.write_bytes(response.content)
                    logger.info(f"✓ Saved capture to {filename}")
                else:
                    logger.error(f"Failed to capture from {camera.name}: HTTP {response.status_code}")

            except Exception as e:
                logger.error(f"Error capturing from {camera.name}: {e}")

    # Camera management
    def get_cameras(self, search_network: bool = True) -> List['Camera']:
        """Get list of cameras in this room."""
        if search_network:
            # scan network for espressif devices using nmap
            cameras = discover_cameras()
            for camera in cameras:
                camera = Camera(
                    room_id= self.room_id,
                    name=camera["name"],
                    ip=camera["ip"],
                    port=camera["port"],
                    mac=camera["mac"],
                url=camera["url"],
                stream_url=camera["stream_url"]
            )
            self.add_camera(camera) # type: ignore
        return self._cameras.copy()

    def add_camera(self, camera: 'Camera') -> None:
        """
        Add a camera to this room.

        Args:
            camera: Camera instance to add
        """
        if camera not in self._cameras:
            self._cameras.append(camera)
            logger.debug(f"Added camera {camera.name} to room {self._name}")
        else:
            logger.warning(f"Camera {camera.name} already in room {self._name}")

    def remove_camera(self, camera: 'Camera') -> None:
        """
        Remove a camera from this room.

        Args:
            camera: Camera instance to remove
        """
        if camera in self._cameras:
            self._cameras.remove(camera)
            logger.debug(f"Removed camera {camera.name} from room {self._name}")
        else:
            logger.warning(f"Camera {camera.name} not found in room {self._name}")

    def set_camera_inactive(self, camera: 'Camera') -> None:
        """
        Force a camera to inactive state.

        Args:
            camera: Camera instance to deactivate
        """
        if camera in self._cameras:
            from .camera_state import CameraStatus
            #FIXME actually turn the camera off instead of just setting status
            camera.set_status(CameraStatus.INACTIVE, reason="Forced by room controller")
            logger.debug(f"Forced camera {camera.name} inactive in room {self.name}")
        else:
            logger.warning(f"Camera {camera.name} not found in room {self.name}")

    # Person detector management
    def get_person_detectors(self, search_network: bool = True) -> List['PersonDetector']:
        """Get list of person detectors in this room."""
        if search_network:
            pds = discover_presence_sensors()
            for pd in pds:
                detector = PersonDetector(
                    name=pd["name"],
                host=pd["ip"],
                port=pd["port"],
                room=self  # Pass room reference so detector can update room state
            )
            self.add_person_detector(detector) # type: ignore
        return self._person_detectors.copy()

    def add_person_detector(self, detector: 'PersonDetector') -> None:
        """
        Add a person detector to this room. Ignore duplicates.

        Args:
            detector: PersonDetector instance to add
        """
        if detector not in self._person_detectors:
            self._person_detectors.append(detector)
            logger.debug(f"Added detector {detector.name} to room {self._name}")
        else:
            logger.warning(f"Detector {detector.name} already in room {self._name}")

    def remove_person_detector(self, detector: 'PersonDetector') -> None:
        """
        Remove a person detector from this room.

        Args:
            detector: PersonDetector instance to remove
        """
        if detector in self._person_detectors:
            self._person_detectors.remove(detector)
            logger.debug(f"Removed detector {detector.name} from room {self.name}")
        else:
            logger.warning(f"Detector {detector.name} not found in room {self.name}")