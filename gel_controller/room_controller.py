"""
RoomController - Orchestrates multiple rooms with cameras and person detectors.
"""

import asyncio
import threading
from typing import List, Dict
import logging

logger = logging.getLogger(__name__)


class RoomController:
    """
    Main controller that manages multiple rooms.

    Coordinates:
    - Multiple Room instances
    - Starting/stopping all cameras and detectors
    - Graceful shutdown
    """
    def __init__(self):
        """Initialize the RoomController with empty room list."""
        self._rooms: List['Room'] = []
        self._running = False
        self._threads: List[threading.Thread] = []
        self._event_loop = None
        self._shutdown_event = asyncio.Event()

    def get_rooms(self) -> List['Room']:
        """
        Get list of all rooms managed by this controller.

        Returns:
            List of Room instances
        """
        return self._rooms.copy()

    def add_room(self, room: 'Room') -> None:
        """
        Add a room to the controller.

        Args:
            room: Room instance to add
        """
        if room not in self._rooms:
            self._rooms.append(room)
            logger.info(f"Added room: {room.name} (ID: {room.room_id})")
        else:
            logger.warning(f"Room {room.name} already exists in controller")

    def remove_room(self, room: 'Room') -> None:
        """
        Remove a room from the controller.

        Args:
            room: Room instance to remove
        """
        if room in self._rooms:
            self._rooms.remove(room)
            logger.info(f"Removed room: {room.name} (ID: {room.get_room_id()})")
        else:
            logger.warning(f"Room {room.name} not found in controller")

    def start(self) -> None:
        """
        Start all cameras and person detectors in all rooms.

        Creates threads for:
        - Each camera's monitoring loop
        - Each person detector's monitoring loop
        """
        if self._running:
            logger.warning("Controller is already running")
            return

        self._running = True
        self._shutdown_event.clear()

        logger.info(f"Starting RoomController with {len(self._rooms)} room(s)")

        # Start all cameras and detectors in all rooms
        for room in self._rooms:
            # Start cameras
            for camera in room.get_cameras():
                thread = threading.Thread(
                    target=self._run_camera_loop,
                    args=(camera, room),
                    name=f"Camera-{camera.name}",
                    daemon=True
                )
                thread.start()
                self._threads.append(thread)
                logger.debug(f"Started thread for camera: {camera.name}")

            # Start person detectors
            for detector in room.get_person_detectors():
                thread = threading.Thread(
                    target=self._run_detector_loop,
                    args=(detector,),
                    name=f"Detector-{detector.name}",
                    daemon=True
                )
                thread.start()
                self._threads.append(thread)
                logger.debug(f"Started thread for detector: {detector.name}")

        logger.info(f"Started {len(self._threads)} thread(s)")

    def _run_camera_loop(self, camera: 'Camera', room: 'Room') -> None:
        """
        Camera monitoring loop (runs in separate thread).

        Args:
            camera: Camera instance to monitor
            room: Room instance the camera belongs to
        """
        try:
            while self._running:
                # Camera checks room state and updates itself
                camera.check_room_and_update_state(room)

                # Output status if active
                if camera.get_state() == "active":
                    camera.output_status()

                # Sleep for poll interval
                import time
                time.sleep(camera.get_poll_interval())
        except Exception as e:
            logger.error(f"Error in camera loop for {camera.name}: {e}")

    def _run_detector_loop(self, detector: 'PersonDetector') -> None:
        """
        Person detector monitoring loop (runs in separate thread).

        Args:
            detector: PersonDetector instance to monitor
        """
        try:
            # Create event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            # Run async detector
            loop.run_until_complete(self._async_detector_loop(detector))
        except Exception as e:
            logger.error(f"Error in detector loop for {detector.name}: {e}")
        finally:
            loop.close()

    async def _async_detector_loop(self, detector: 'PersonDetector') -> None:
        """
        Async person detector monitoring loop.

        Args:
            detector: PersonDetector instance to monitor
        """
        try:
            # Connect to device
            await detector.connect()

            # Subscribe to state changes
            await detector.subscribe_to_states()

            # Keep running and checking for timeouts
            while self._running:
                detector.check_heartbeat_timeout()
                await asyncio.sleep(1)  # Check every second

        except Exception as e:
            logger.error(f"Error in async detector loop for {detector.name}: {e}")
        finally:
            await detector.disconnect()

    def shutdown(self) -> None:
        """
        Gracefully shutdown all cameras and person detectors.

        Stops all monitoring threads and disconnects from devices.
        """
        if not self._running:
            logger.warning("Controller is not running")
            return

        logger.info("Shutting down RoomController...")
        self._running = False
        self._shutdown_event.set()

        # Wait for all threads to finish
        for thread in self._threads:
            thread.join(timeout=5.0)
            if thread.is_alive():
                logger.warning(f"Thread {thread.name} did not stop gracefully")

        self._threads.clear()
        logger.info("RoomController shutdown complete")

    def is_running(self) -> bool:
        """
        Check if the controller is currently running.

        Returns:
            True if running, False otherwise
        """
        return self._running
