"""
Camera - Represents a camera device that monitors a room.
"""

import logging
import time
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class Camera:
    """
    Represents a camera device.

    Cameras:
    - Poll their room to check if they can activate
    - Only activate when room is empty (privacy-respecting)
    - Output "Camera n active" when active
    """

    def __init__(
        self,
        name: str,
        room_id: str,
        initial_state: str = "inactive",
        poll_interval: float = 10.0,
        output_interval: float = 10.0
    ):
        """
        Initialize a Camera.

        Args:
            name: Camera name
            room_id: ID of the room this camera belongs to
            initial_state: Initial state (default: "inactive")
            poll_interval: How often to poll room state in seconds (default: 10.0)
            output_interval: How often to output status in seconds (default: 10.0)

        Raises:
            ValueError: If initial_state is invalid
        """
        self._name = name
        self._room_id = room_id
        self._state = None
        self._poll_interval = poll_interval
        self._output_interval = output_interval
        self._last_output_time = 0.0

        # Validate and set initial state
        self.set_state(initial_state)

    # Name getters/setters
    def get_name(self) -> str:
        """Get camera name."""
        return self._name

    def set_name(self, name: str) -> None:
        """Set camera name."""
        self._name = name

    # Room ID getters/setters
    def get_room_id(self) -> str:
        """Get room ID."""
        return self._room_id

    def set_room_id(self, room_id: str) -> None:
        """Set room ID."""
        self._room_id = room_id

    # State getters/setters
    def get_state(self) -> str:
        """Get current camera state."""
        return self._state

    def set_state(self, state: str) -> None:
        """
        Set camera state.

        Args:
            state: New state ("active" or "inactive")

        Raises:
            ValueError: If state is invalid
        """
        valid_states = ["active", "inactive"]
        if state not in valid_states:
            raise ValueError(f"Invalid state: {state}. Must be one of {valid_states}")

        old_state = self._state
        self._state = state

        if old_state != state:
            logger.debug(f"Camera {self._name} state changed: {old_state} → {state}")

    # Poll interval getters/setters
    def get_poll_interval(self) -> float:
        """Get poll interval in seconds."""
        return self._poll_interval

    def set_poll_interval(self, interval: float) -> None:
        """Set poll interval in seconds."""
        self._poll_interval = interval

    # Output interval getters/setters
    def get_output_interval(self) -> float:
        """Get output interval in seconds."""
        return self._output_interval

    def set_output_interval(self, interval: float) -> None:
        """Set output interval in seconds."""
        self._output_interval = interval

    def check_room_and_update_state(self, room: 'Room') -> None:
        """
        Poll room state and update camera state accordingly.

        Camera becomes active only if room is empty.
        Camera becomes inactive if room is occupied.

        Args:
            room: Room instance to check
        """
        room_state = room.get_state()

        if room_state == "empty":
            # Room is empty, camera can activate
            self.set_state("active")
        elif room_state == "occupied":
            # Room is occupied, camera must deactivate
            self.set_state("inactive")

    def output_status(self) -> None:
        """
        Output camera status if active.

        Only outputs if camera is active and output interval has passed.
        """
        current_time = time.time()

        # Check if enough time has passed since last output
        if current_time - self._last_output_time >= self._output_interval:
            if self._state == "active":
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"[{timestamp}] {self._name} active")
                self._last_output_time = current_time
