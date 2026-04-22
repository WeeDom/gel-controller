"""
PersonDetector - Monitors ESPHome device for heartbeat data to detect occupancy.
"""

import logging
import threading
import time
import asyncio
from typing import Optional, TYPE_CHECKING
from aioesphomeapi.client import APIClient
from aioesphomeapi.core import TimeoutAPIError

if TYPE_CHECKING:
    from .room import Room

logger = logging.getLogger(__name__)


class PersonDetector:
    __slots__ = (
        "_ip", "_name", "_port", "_host", "_encryption_key",
        "_heartbeat_timeout", "_last_heartbeat_time",
        "_api_client", "_heartbeat_sensor_key", "_room",
        "_disconnect_event",
        "_presence_sensor_key", "_presence_detected",
        "_presence_confirmed_timeout", "_presence_cleared_at", "_empty_confirm_timer")

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
        heartbeat_timeout: float = 300.0
    ):
        """
        Initialize a PersonDetector.

        Args:
            name: Detector name
            host: ESPHome device hostname or IP
            port: ESPHome API port (default: 6053)
            encryption_key: Optional encryption key for secure connection
            heartbeat_timeout: Timeout in seconds before considering room empty (default: 300.0 = 5 minutes)
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
        self._presence_sensor_key: Optional[int] = None
        self._presence_detected: bool = False
        self._presence_confirmed_timeout: float = 120.0  # seconds after both signals clear before declaring empty
        self._presence_cleared_at: Optional[float] = None
        self._empty_confirm_timer: Optional[threading.Timer] = None
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

            # Reset all presence state on (re)connect so stale timers don't carry over
            self._cancel_empty_confirm_timer()
            self._presence_cleared_at = None
            # Find the heartbeat sensor and the has_target presence binary sensor
            self._presence_sensor_key = None
            self._presence_detected = False
            for entity in entities:
                print(f"  - Entity: {entity.name} (key: {entity.key}, type: {type(entity).__name__})")
                name_lower = entity.name.lower() if hasattr(entity, 'name') else ""
                if 'heart rate' in name_lower and self._heartbeat_sensor_key is None:
                    self._heartbeat_sensor_key = entity.key
                    logger.info(f"Found heartbeat sensor: {entity.name} (key: {entity.key})")
                elif 'person information' in name_lower or 'has_target' in name_lower:
                    self._presence_sensor_key = entity.key
                    logger.info(f"Found presence sensor: {entity.name} (key: {entity.key})")

            if self._heartbeat_sensor_key is None:
                logger.warning(f"No heartbeat sensor found on device {self._host}")
            if self._presence_sensor_key is None:
                logger.warning(f"No presence (has_target) sensor found on device {self._host} — empty detection will rely on heart rate alone")

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
            except TimeoutAPIError as e:
                logger.warning(
                    "Detector %s graceful disconnect timed out for %s:%s (%s); forcing disconnect",
                    self._name,
                    self._host,
                    self._port,
                    e,
                )
                try:
                    await self._api_client.disconnect(force=True)
                except Exception as force_error:
                    logger.debug(
                        "Detector %s forced disconnect also failed for %s:%s: %s",
                        self._name,
                        self._host,
                        self._port,
                        force_error,
                    )
            except Exception as e:
                logger.error(f"Error disconnecting detector {self._name}: {e}")
            finally:
                self._disconnect_event = None
                self._cancel_empty_confirm_timer()

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
        # Primary occupancy gate: has_target binary sensor ("Person Information")
        if self._presence_sensor_key is not None and state.key == self._presence_sensor_key:
            self._presence_detected = bool(state.state)
            logger.info(f"👤 Presence sensor update: {'DETECTED' if self._presence_detected else 'CLEARED'}")
            if self._presence_detected:
                # Person re-detected — cancel any pending empty confirmation
                self._cancel_empty_confirm_timer()
                self._presence_cleared_at = None
                if self.room:
                    self.room.state = "occupied"
            else:
                # Presence cleared — start an event-driven confirmation window.
                # If both this AND heartbeat stay absent for _presence_confirmed_timeout
                # seconds we declare empty without waiting for the full heartbeat_timeout.
                self._presence_cleared_at = time.time()
                self._start_empty_confirm_timer()
            return

        # Secondary signal: heart rate (keeps the timeout clock alive while person is still)
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
            # Heartbeat means someone is present — cancel any empty confirmation in flight
            self._cancel_empty_confirm_timer()
            self._presence_cleared_at = None

            # Update room state to occupied
            if self.room:
                self.room.state = "occupied"
                logger.info(f"Detector {self._name} set room to occupied (HR: {heart_rate} bpm)")

    def on_heartbeat_timeout(self) -> None:
        """
        Handle heartbeat timeout (no heartbeat detected).

        Sets room state to empty only when the has_target presence sensor also
        reports no target.  If the presence sensor is still active the room is
        NOT declared empty — this prevents photos being taken while someone is
        in the room but momentarily not producing a detectable heart rate.
        """
        if self._presence_sensor_key is not None and self._presence_detected:
            logger.warning(
                f"⚠️  Detector {self._name}: heart rate timed out but presence sensor "
                f"still active — NOT declaring room empty (person still detected)"
            )
            # Reset the heartbeat clock so we don't spin on this warning every second.
            self._last_heartbeat_time = time.time()
            return

        self._cancel_empty_confirm_timer()
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
        if not self.has_heartbeat_timed_out() or self._last_heartbeat_time is None:
            return

        time_since_heartbeat = time.time() - self._last_heartbeat_time
        logger.info(f"⏱️  Heartbeat timeout after {time_since_heartbeat:.1f}s → Setting room to EMPTY")
        self.on_heartbeat_timeout()

    def _start_empty_confirm_timer(self) -> None:
        """Start the event-driven empty-confirmation timer (cancels any existing one)."""
        self._cancel_empty_confirm_timer()
        self._empty_confirm_timer = threading.Timer(
            self._presence_confirmed_timeout, self._check_empty_confirmed
        )
        self._empty_confirm_timer.daemon = True
        self._empty_confirm_timer.start()
        logger.info(
            f"⏳ Detector {self._name}: presence cleared — will confirm empty in "
            f"{self._presence_confirmed_timeout:.0f}s if no signals return"
        )

    def _cancel_empty_confirm_timer(self) -> None:
        """Cancel any pending empty-confirmation timer."""
        if self._empty_confirm_timer is not None:
            self._empty_confirm_timer.cancel()
            self._empty_confirm_timer = None

    def _check_empty_confirmed(self) -> None:
        """
        Fired by the empty-confirm timer thread after _presence_confirmed_timeout seconds.

        Declares the room empty only if BOTH signals are still absent.  This is the
        event-driven path — no polling required.
        """
        self._empty_confirm_timer = None

        if self._presence_detected:
            logger.info(
                f"Detector {self._name}: presence returned during confirmation window — "
                f"aborting empty transition"
            )
            return

        if self._last_heartbeat_time is not None:
            elapsed = time.time() - self._last_heartbeat_time
            if elapsed < self._presence_confirmed_timeout:
                logger.info(
                    f"Detector {self._name}: presence clear but heartbeat was {elapsed:.1f}s ago — "
                    f"aborting empty transition"
                )
                return

        logger.info(
            f"✅ Detector {self._name}: both signals absent for "
            f"{self._presence_confirmed_timeout:.0f}s — declaring room EMPTY (event-driven)"
        )
        self.on_heartbeat_timeout()
