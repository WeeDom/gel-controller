"""
PersonDetector - Monitors ESPHome device for heartbeat data to detect occupancy.
"""

import logging
import time
import asyncio
from typing import Optional, TYPE_CHECKING
from aioesphomeapi.client import APIClient

if TYPE_CHECKING:
    from .room import Room

logger = logging.getLogger(__name__)


class PersonDetector:
    __slots__ = (
        "_ip", "_name", "_port", "_host", "_encryption_key",
        "_heartbeat_timeout", "_last_heartbeat_time",
        "_api_client", "_heartbeat_sensor_key", "_room",
        "_disconnect_event")

    """
    Person detector using ESPHome device with heartbeat sensor.

    Detects room occupancy by monitoring real-time heart rate sensor.
    Updates room state based on heartbeat detection.
    """

    def __init__(
        self,
        name: str,
        host: str,
        port: int = 6053,
        room: Optional['Room'] = None,
        encryption_key: Optional[str] = None,
        heartbeat_timeout: float = 10.0
    ):
        """
        Initialize a PersonDetector.

        Args:
            name: Detector name
            host: ESPHome device hostname or IP
            port: ESPHome API port (default: 6053)
            encryption_key: Optional encryption key for secure connection
            heartbeat_timeout: Timeout in seconds before considering room empty (default: 10.0)
        """
        self._name = name
        self._host = host
        self._port = port
        self._room = room
        self._encryption_key = encryption_key
        self._heartbeat_timeout = heartbeat_timeout
        self._last_heartbeat_time: Optional[float] = None
        self._api_client: Optional[APIClient] = None
        self._heartbeat_sensor_key: Optional[int] = None
        self._disconnect_event: Optional[asyncio.Event] = None

    ## mutable
    @property
    def name(self) -> str:
        """Get detector name."""
        return self._name

    @name.setter
    def name(self, name: str) -> None:
        """Set detector name."""
        self._name = name

    def get_name(self) -> str:
        """Backward-compatible getter for detector name."""
        return self.name

    def set_name(self, name: str) -> None:
        """Backward-compatible setter for detector name."""
        self.name = name

    @property
    def heartbeat_timeout(self) -> float:
        """Get heartbeat timeout in seconds."""
        return self._heartbeat_timeout

    @heartbeat_timeout.setter
    def heartbeat_timeout(self, timeout: float) -> None:
        """Set heartbeat timeout in seconds."""
        self._heartbeat_timeout = timeout

    def get_heartbeat_timeout(self) -> float:
        """Backward-compatible getter for heartbeat timeout."""
        return self.heartbeat_timeout

    def set_heartbeat_timeout(self, timeout: float) -> None:
        """Backward-compatible setter for heartbeat timeout."""
        self.heartbeat_timeout = timeout

    @property
    def room(self) -> Optional['Room']:
        """Get associated room."""
        return self._room

    @room.setter
    def room(self, room: 'Room') -> None:
        """Set associated room."""
        self._room = room

    def get_room(self) -> Optional['Room']:
        """Backward-compatible getter for associated room."""
        return self.room

    def set_room(self, room: 'Room') -> None:
        """Backward-compatible setter for associated room."""
        self.room = room

    ## immutable
    @property
    def ip(self) -> str:
        """Get device IP address."""
        return self._host

    # Host getters/setters
    @property
    def host(self) -> str:
        """Get ESPHome device host."""
        return self._host

    @host.setter
    def host(self, host: str) -> None:
        """Set ESPHome device host."""
        self._host = host

    def get_host(self) -> str:
        """Backward-compatible getter for host."""
        return self.host

    def set_host(self, host: str) -> None:
        """Backward-compatible setter for host."""
        self.host = host

    # Port getters/setters
    @property
    def port(self) -> int:
        """Get ESPHome API port."""
        return self._port

    @port.setter
    def port(self, port: int) -> None:
        """Set ESPHome API port."""
        self._port = port

    def get_port(self) -> int:
        """Backward-compatible getter for port."""
        return self.port

    def set_port(self, port: int) -> None:
        """Backward-compatible setter for port."""
        self.port = port

    async def connect(self) -> None:
        """
        Connect to ESPHome device.

        Establishes connection and retrieves device entities.
        """
        try:
            self._heartbeat_sensor_key = None
            self._disconnect_event = asyncio.Event()
            self._api_client = APIClient(
                self._host,
                self._port,
                None,  # password (deprecated)
                noise_psk=self._encryption_key
            )

            await self._api_client.connect(on_stop=self._on_connection_stop, login=True)
            logger.info(f"Detector {self._name} connected to {self._host}:{self._port}")

            # Get device entities to find heartbeat sensor
            entities, services = await self._api_client.list_entities_services()

            # Find the heartbeat sensor
            for entity in entities:
                print(f"  - Entity: {entity.name} (key: {entity.key}, type: {type(entity).__name__})")
                if hasattr(entity, 'name') and 'heart rate' in entity.name.lower():
                    self._heartbeat_sensor_key = entity.key
                    logger.info(f"Found heartbeat sensor: {entity.name} (key: {entity.key})")
                    break

            if self._heartbeat_sensor_key is None:
                logger.warning(f"No heartbeat sensor found on device {self._host}")

        except Exception as e:
            logger.error(f"Failed to connect detector {self._name} to {self._host}: {e}")
            raise

    async def _on_connection_stop(self, expected_disconnect: bool) -> None:
        """Receive API client stop notifications and surface unexpected drops."""
        if not expected_disconnect:
            logger.warning(
                "Detector %s connection lost for %s:%s; scheduling reconnect",
                self._name,
                self._host,
                self._port,
            )
        if self._disconnect_event is not None:
            self._disconnect_event.set()

    async def disconnect(self) -> None:
        """Disconnect from ESPHome device."""
        if self._api_client:
            try:
                await self._api_client.disconnect()
                logger.info(f"Detector {self._name} disconnected from {self._host}")
            except Exception as e:
                logger.error(f"Error disconnecting detector {self._name}: {e}")
            finally:
                self._disconnect_event = None

    async def wait_for_disconnect(self, timeout: float) -> bool:
        """
        Wait for API disconnect notification.

        Returns True if disconnected, False if timeout elapsed with no disconnect.
        """
        if self._disconnect_event is None:
            return False

        try:
            await asyncio.wait_for(self._disconnect_event.wait(), timeout=max(0.0, timeout))
            return True
        except asyncio.TimeoutError:
            return False

    def has_heartbeat_timed_out(self) -> bool:
        """Return True when last heartbeat exceeds configured timeout."""
        if self._last_heartbeat_time is None:
            logger.debug("No heartbeat detected yet (last_heartbeat_time is None)")
            return False

        time_since_heartbeat = time.time() - self._last_heartbeat_time
        logger.debug(
            "Checking timeout: last heartbeat %.1fs ago (timeout=%ss)",
            time_since_heartbeat,
            self._heartbeat_timeout,
        )
        return time_since_heartbeat > self._heartbeat_timeout

    async def probe_sensor_alive(self, timeout: float = 2.0) -> bool:
        """
        Actively probe the sensor via API.

        Returns True when device responds to API query, otherwise False.
        """
        if self._api_client is None:
            return False

        try:
            await asyncio.wait_for(self._api_client.device_info(), timeout=max(0.1, timeout))
            return True
        except Exception as e:
            logger.warning(
                "Detector %s liveness probe failed for %s:%s: %s",
                self._name,
                self._host,
                self._port,
                e,
            )
            return False

    async def subscribe_to_states(self) -> None:
        """
        Subscribe to ESPHome device state changes.

        Registers callback to handle sensor state updates.
        """
        if not self._api_client:
            raise RuntimeError("Not connected to device. Call connect() first.")

        try:
            self._api_client.subscribe_states(self.handle_state_change)
            logger.info(f"Detector {self._name} subscribed to state changes")
        except Exception as e:
            logger.error(f"Failed to subscribe to states: {e}")
            raise

    def handle_state_change(self, state) -> None:
        """
        Handle sensor state change from ESPHome device.

        Args:
            state: State object from aioesphomeapi
        """
        # Check if this is the heartbeat sensor
        if self._heartbeat_sensor_key is not None and state.key == self._heartbeat_sensor_key:
            heart_rate = float(state.state)
            logger.info(f"💓 Heartbeat sensor update: {heart_rate} bpm")

            # Only consider valid heartbeat if rate > 0
            if heart_rate > 0:
                self.on_heartbeat_detected(heart_rate)

    def on_heartbeat_detected(self, heart_rate: float) -> None:
        """
        Handle heartbeat detection.

        Args:
            heart_rate: Detected heart rate in bpm
        """
        if heart_rate > 0:
            self._last_heartbeat_time = time.time()

            # Update room state to occupied
            if self.room:
                self.room.state = "occupied"
                logger.info(f"Detector {self._name} set room to occupied (HR: {heart_rate} bpm)")

    def on_heartbeat_timeout(self) -> None:
        """
        Handle heartbeat timeout (no heartbeat detected).

        Sets room state to empty.
        """
        self._last_heartbeat_time = None

        # Update room state to empty
        if self._room:
            logger.info(f"🚪 Detector {self._name} setting room to EMPTY (timeout)")
            self._room.state = "empty"
        else:
            logger.warning(f"Detector {self._name} has no room assigned!")

    def check_heartbeat_timeout(self) -> None:
        """
        Check if heartbeat has timed out.

        Should be called periodically to detect when heartbeat stops.
        """
        if not self.has_heartbeat_timed_out():
            return

        time_since_heartbeat = time.time() - float(self._last_heartbeat_time)
        logger.info(f"⏱️  Heartbeat timeout after {time_since_heartbeat:.1f}s → Setting room to EMPTY")
        self.on_heartbeat_timeout()
