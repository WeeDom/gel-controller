"""
Camera - Represents a camera device that monitors a room.
"""

import logging
import time
from datetime import datetime
from typing import Optional, TYPE_CHECKING
from pathlib import Path
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

        self._camera_state = CameraState(resolved_status, camera=self)
        self._poll_interval = poll_interval
        self._output_interval = output_interval
        self._last_output_time = 0.0
        self._saw_occupied = False
        self.capture_count = 0
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

        Camera remains inactive for privacy. Capture triggering is coordinated
        at room-transition level (occupied -> empty), not per camera poll loop.

        Args:
            room: Room instance to check
        """
        room_state = room.state

        if room_state == "occupied":
            self._saw_occupied = True
            # Occupied room always forces camera inactive.
            if self.status != CameraStatus.INACTIVE:
                self.set_status(CameraStatus.INACTIVE, "Room occupied")
            return

        # Room is empty.
        if self.status == CameraStatus.OFFLINE:
            self.set_status(CameraStatus.INACTIVE, "Room empty (offline->inactive)")
            return

        if self.status != CameraStatus.INACTIVE:
            self.set_status(CameraStatus.INACTIVE, "Room empty (idle)")

        self._saw_occupied = False

    def capture_image(self, room: 'Room', tag: str = "capture") -> Optional[Path]:
        """Capture a single image frame from the camera HTTP endpoint."""
        if not self.ip:
            logger.warning(f"Camera {self._name} has no IP; skipping capture")
            return None

        import requests

        capture_dir = Path("captures")
        capture_dir.mkdir(exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        control_url = f"http://{self.ip}/control"
        capture_url = f"http://{self.ip}/capture"

        try:
            control_response = requests.get(
                control_url,
                params={"var": "framesize", "val": 15},
                timeout=5,
                headers={
                    'User-Agent': 'GEL-Controller/1.0'
                }
            )
            if control_response.status_code != 200:
                logger.warning(
                    f"Failed to set framesize on {self._name}: HTTP {control_response.status_code}; continuing with capture"
                )

            logger.info(f"Capturing from {self._name} at {capture_url}")
            response = requests.get(
                capture_url,
                timeout=10,
                headers={
                    'User-Agent': 'GEL-Controller/1.0'
                }
            )

            if response.status_code == 200:
                filename = capture_dir / f"{tag}-{room.room_id}-{self._name}-{timestamp}.jpeg"
                filename.write_bytes(response.content)
                logger.info(f"✓ Saved capture to {filename}")
                self.capture_count += 1
                return filename
            else:
                logger.error(f"Failed to capture from {self._name}: HTTP {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"Error capturing from {self._name}: {e}")
            return None

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
