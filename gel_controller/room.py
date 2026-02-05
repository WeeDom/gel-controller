"""
Room - Represents a physical room with state management.
"""

import logging
from turtle import pd
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


    @property
    def name(self) -> str:
        """Get room name."""
        return self._name

    @name.setter
    def name(self, name: str) -> None:
        """Set room name."""
        self._name = name

    @property
    def state(self) -> str:
        """Get current room state."""
        return self._state

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
        self._state = state

        if old_state != state:
            logger.info(f"Room {self._name} state changed: {old_state} → {state}")

    def get_state(self) -> str:
        """Get current room state."""
        return self._state

    def set_state(self, state: str) -> None:
        """Set room state using the property setter."""
        self.state = state

    # Camera management
    def get_cameras(self) -> List['Camera']:
        """Get list of cameras in this room."""
        # scan network for espressif devices using nmap
        cameras = discover_cameras()
        for camera in cameras:
            camera = Camera(
                room_id= self.room_id,
                ip=camera["ip"],
                port=camera["port"],
                mac=camera["mac"],
                url=camera["url"],
                stream_url=camera["stream_url"]
            )
            self.add_camera(camera)
        return self._cameras.copy()

    def add_camera(self, camera: 'Camera') -> None:
        """
        Add a camera to this room.

        Args:
            camera: Camera instance to add
        """
        if camera not in self._cameras:
            self._cameras.append(camera)
            logger.debug(f"Added camera {camera.get_name()} to room {self._name}")
        else:
            logger.warning(f"Camera {camera.get_name()} already in room {self._name}")

    def remove_camera(self, camera: 'Camera') -> None:
        """
        Remove a camera from this room.

        Args:
            camera: Camera instance to remove
        """
        if camera in self._cameras:
            self._cameras.remove(camera)
            logger.debug(f"Removed camera {camera.get_name()} from room {self._name}")
        else:
            logger.warning(f"Camera {camera.get_name()} not found in room {self._name}")

    def set_camera_inactive(self, camera: 'Camera') -> None:
        """
        Force a camera to inactive state.

        Args:
            camera: Camera instance to deactivate
        """
        if camera in self._cameras:
            camera.set_state("inactive")
            logger.debug(f"Forced camera {camera.get_name()} inactive in room {self._name}")
        else:
            logger.warning(f"Camera {camera.get_name()} not found in room {self._name}")

    # Person detector management
    def get_person_detectors(self) -> List['PersonDetector']:
        """Get list of person detectors in this room."""
        pds = discover_presence_sensors()
        for pd in pds:
            pd = PersonDetector(
                name=pd["name"],
                host=pd["ip"],
                port=pd["port"]
            )
            self.add_person_detector(pd)
        return self._person_detectors.copy()

    def add_person_detector(self, detector: 'PersonDetector') -> None:
        """
        Add a person detector to this room. Ignore duplicates.

        Args:
            detector: PersonDetector instance to add
        """
        if detector not in self._person_detectors:
            self._person_detectors.append(detector)
            logger.debug(f"Added detector {detector.get_name()} to room {self._name}")
        else:
            logger.warning(f"Detector {detector.get_name()} already in room {self._name}")

    def remove_person_detector(self, detector: 'PersonDetector') -> None:
        """
        Remove a person detector from this room.

        Args:
            detector: PersonDetector instance to remove
        """
        if detector in self._person_detectors:
            self._person_detectors.remove(detector)
            logger.debug(f"Removed detector {detector.get_name()} from room {self._name}")
        else:
            logger.warning(f"Detector {detector.get_name()} not found in room {self._name}")
