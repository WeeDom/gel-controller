"""
Camera - Represents a camera device that monitors a room.
"""

import logging
import time
from datetime import datetime
from typing import Optional, TYPE_CHECKING
from .camera_state import CameraState, CameraStatus

if TYPE_CHECKING:
    from .room import Room

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
        initial_status: CameraStatus = CameraStatus.OFFLINE,
        poll_interval: float = 10.0,
        output_interval: float = 10.0,
        mac: Optional[str] = None,
        url: Optional[str] = None,
        stream_url: Optional[str] = None,
        ip: Optional[str] = None,
        port: Optional[int] = None,
        state: Optional[str] = None
    ):
        """
        Initialize a Camera.

        Args:
            name: Camera name
            room_id: ID of the room this camera belongs to
            initial_status: Initial status (default: OFFLINE)
            poll_interval: How often to poll room state in seconds (default: 10.0)
            output_interval: How often to output status in seconds (default: 10.0)

        Raises:
            ValueError: If initial_status is invalid
        """
        self._name = name
        self._room_id = room_id
        resolved_status = initial_status
        if state is not None:
            if isinstance(state, CameraStatus):
                resolved_status = state
            elif isinstance(state, str):
                try:
                    resolved_status = CameraStatus(state.lower())
                except ValueError:
                    logger.warning(f"Unknown camera state '{state}', using {initial_status.value}")

        self._camera_state = CameraState(resolved_status)
        self._poll_interval = poll_interval
        self._output_interval = output_interval
        self._last_output_time = 0.0
        self._ip = ip
        self.mac = mac
        self.url = url
        self.stream_url = stream_url
        self._port = port

    # Name property
    @property
    def name(self) -> str:
        """Get camera name."""
        return self._name

    @name.setter
    def name(self, name: str) -> None:
        """Set camera name."""
        self._name = name

    # Room ID property
    @property
    def room_id(self) -> str:
        """Get room ID."""
        return self._room_id

    @property
    def ip(self) -> Optional[str]:
        """Get camera IP address."""
        return self._ip

    @room_id.setter
    def room_id(self, room_id: str) -> None:
        """Set room ID."""
        self._room_id = room_id

    @property
    def status(self) -> CameraStatus:
        """Get current camera status."""
        return self._camera_state.status

    @property
    def status_value(self) -> str:
        """Get current status as string."""
        return self._camera_state.status_value

    def set_status(self, new_status: CameraStatus, reason: Optional[str] = None) -> bool:
        """
        Set camera status with transition validation.

        Args:
            new_status: Target status
            reason: Optional reason for transition

        Returns:
            True if transition successful
        """
        return self._camera_state.transition_to(new_status, reason)

    @property
    def poll_interval(self) -> float:
        """Get poll interval in seconds."""
        return self._poll_interval

    @poll_interval.setter
    def poll_interval(self, interval: float) -> None:
        """Set poll interval in seconds."""
        self._poll_interval = interval

    # Output interval property
    @property
    def output_interval(self) -> float:
        """Get output interval in seconds."""
        return self._output_interval

    @output_interval.setter
    def output_interval(self, interval: float) -> None:
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
        room_state = room.state

        if room_state == "occupied":
            # Occupied room always forces camera inactive.
            if self.status != CameraStatus.INACTIVE:
                self.set_status(CameraStatus.INACTIVE, "Room occupied")
            return

        # Room is empty: activate camera when possible.
        if self.status == CameraStatus.OFFLINE:
            # OFFLINE cannot transition directly to ACTIVE.
            self.set_status(CameraStatus.INACTIVE, "Room empty")

        if self.status == CameraStatus.INACTIVE:
            self.set_status(CameraStatus.ACTIVE, "Room empty")

    def output_status(self) -> None:
        """
        Output camera status if active.

        Only outputs if camera is active and output interval has passed.
        """
        current_time = time.time()

        # Check if enough time has passed since last output
        if current_time - self._last_output_time >= self._output_interval:
            if self.status == CameraStatus.ACTIVE:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"[{timestamp}] {self._name} active")
                self._last_output_time = current_time
