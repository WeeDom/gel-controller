"""
RoomController - Orchestrates multiple rooms with cameras and person detectors.
"""

import asyncio
import threading
import sqlite3
import os
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, TYPE_CHECKING, Optional
import logging
from .control_api import ControlAPIServer

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .room import Room
    from .camera import Camera
    from .person_detector import PersonDetector

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
        self._control_server: Optional[ControlAPIServer] = None
        self._control_host = "127.0.0.1"
        self._control_port = 8765
        self._baseline_db_path = Path("logs") / "baselines.db"
        self._spot_diff_enabled = os.getenv("ENABLE_SPOT_THE_DIFF", "1").lower() not in {"0", "false", "no"}
        self._spot_diff_model = os.getenv("SPOT_THE_DIFF_MODEL", "claude-opus-4-5-20251101")
        self._spot_diff_logs_dir = Path("logs") / "spot_the_diff"
        self._detector_poll_interval = float(os.getenv("DETECTOR_POLL_INTERVAL", "1.0"))
        self._detector_probe_timeout = float(os.getenv("DETECTOR_PROBE_TIMEOUT", "2.0"))
        self._detector_reconnect_initial_delay = float(os.getenv("DETECTOR_RECONNECT_INITIAL_DELAY", "2.0"))
        self._detector_reconnect_max_delay = float(os.getenv("DETECTOR_RECONNECT_MAX_DELAY", "60.0"))
        self._init_baseline_db()

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
            room.set_capture_callback(self._on_room_capture_complete)
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
            room.set_capture_callback(None)
            logger.info(f"Removed room: {room.name} (ID: {room.room_id})")
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
        self._start_control_server()

        logger.info(f"Starting RoomController with {len(self._rooms)} room(s)")

        # Start all cameras and detectors in all rooms
        for room in self._rooms:
            # Start cameras
            for camera in room.get_cameras(search_network=False):
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
            for detector in room.get_person_detectors(search_network=False):
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

    def capture_baseline(self, room_id: Optional[str] = None) -> Dict[str, object]:
        """
        Trigger immediate baseline image capture.

        Args:
            room_id: Optional room ID to target a single room

        Returns:
            Summary dict with rooms/cameras targeted
        """
        selected_rooms = self._rooms
        if room_id is not None:
            selected_rooms = [room for room in self._rooms if room.room_id == room_id]

        if not selected_rooms:
            return {"ok": False, "message": "No matching rooms", "rooms": 0, "captures_requested": 0}

        captures_requested = 0
        captures_succeeded = 0
        for room in selected_rooms:
            for camera in room.get_cameras(search_network=False):
                captured = camera.capture_image(room, tag="baseline")
                captures_requested += 1
                if captured:
                    captures_succeeded += 1
                    self._record_baseline_capture(
                        camera_name=camera.name,
                        captured_at=datetime.now().isoformat(),
                        location=room.name,
                    )

        logger.info(
            "Baseline capture requested for %s room(s), %s camera(s)",
            len(selected_rooms),
            captures_requested,
        )
        return {
            "ok": True,
            "rooms": len(selected_rooms),
            "captures_requested": captures_requested,
            "captures_succeeded": captures_succeeded,
            "room_ids": [room.room_id for room in selected_rooms],
        }

    def _init_baseline_db(self) -> None:
        """Initialize baseline SQLite database and schema."""
        self._baseline_db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._baseline_db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS baselines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    camera_name TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    location TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def _record_baseline_capture(self, camera_name: str, captured_at: str, location: str) -> None:
        """Persist baseline metadata (camera name, date, location)."""
        with sqlite3.connect(self._baseline_db_path) as conn:
            conn.execute(
                "INSERT INTO baselines (camera_name, captured_at, location) VALUES (?, ?, ?)",
                (camera_name, captured_at, location),
            )
            conn.commit()

    def _on_room_capture_complete(self, room: 'Room', captured_files: List[Path]) -> None:
        """Kick off asynchronous spot-the-diff analysis for new capture files."""
        if not self._spot_diff_enabled:
            return

        for changeset_path in captured_files:
            thread = threading.Thread(
                target=self._analyze_changeset,
                args=(room, changeset_path),
                name=f"SpotDiff-{room.room_id}-{changeset_path.stem}",
                daemon=True,
            )
            thread.start()

    def analyze_latest(self, room_id: Optional[str] = None) -> Dict[str, object]:
        """Queue spot-the-diff for the latest capture image(s)."""
        if not self._spot_diff_enabled:
            return {"ok": False, "message": "Spot-the-diff is disabled", "queued": 0}

        selected_rooms = self._rooms
        if room_id is not None:
            selected_rooms = [room for room in self._rooms if room.room_id == room_id]

        if not selected_rooms:
            return {"ok": False, "message": "No matching rooms", "queued": 0}

        captures_dir = Path("captures")
        queued = 0
        queued_files: List[str] = []

        for room in selected_rooms:
            room_glob = f"capture-{room.room_id}-*.jp*"
            candidates = list(captures_dir.glob(room_glob))
            if not candidates:
                continue

            latest_changeset = max(candidates, key=lambda p: p.stat().st_mtime)
            thread = threading.Thread(
                target=self._analyze_changeset,
                args=(room, latest_changeset),
                name=f"SpotDiffManual-{room.room_id}-{latest_changeset.stem}",
                daemon=True,
            )
            thread.start()
            queued += 1
            queued_files.append(str(latest_changeset))

        return {
            "ok": True,
            "rooms": len(selected_rooms),
            "queued": queued,
            "queued_files": queued_files,
        }

    def get_status(self, include_logs: bool = True, log_lines: int = 80) -> Dict[str, object]:
        """Return current controller state suitable for remote admin UI."""
        rooms_payload: List[Dict[str, object]] = []

        for room in self._rooms:
            cameras_payload: List[Dict[str, object]] = []
            for camera in room.get_cameras(search_network=False):
                cameras_payload.append(
                    {
                        "name": camera.name,
                        "ip": camera.ip,
                        "status": camera.status_value,
                        "captures": camera.capture_count,
                    }
                )

            detectors_payload: List[Dict[str, object]] = []
            now = time.time()
            for detector in room.get_person_detectors(search_network=False):
                last_heartbeat = getattr(detector, "_last_heartbeat_time", None)
                age_seconds = None
                if isinstance(last_heartbeat, (int, float)):
                    age_seconds = round(max(0.0, now - float(last_heartbeat)), 1)

                detectors_payload.append(
                    {
                        "name": detector.name,
                        "host": detector.host,
                        "port": detector.port,
                        "heartbeat_age_seconds": age_seconds,
                    }
                )

            rooms_payload.append(
                {
                    "room_id": room.room_id,
                    "name": room.name,
                    "state": room.state,
                    "cameras": cameras_payload,
                    "detectors": detectors_payload,
                }
            )

        payload: Dict[str, object] = {
            "ok": True,
            "running": self._running,
            "control_api": f"http://{self._control_host}:{self._control_port}",
            "rooms": rooms_payload,
            "thread_count": len(self._threads),
            "spot_diff_enabled": self._spot_diff_enabled,
            "timestamp": datetime.now().isoformat(),
        }

        if include_logs:
            payload["recent_log_lines"] = self._tail_latest_log(lines=log_lines)

        return payload

    def _tail_latest_log(self, lines: int = 80) -> List[str]:
        """Return trailing lines from the newest gel log file."""
        log_dir = Path("logs")
        if not log_dir.exists():
            return []

        candidates = sorted(log_dir.glob("gel-*.log"), key=lambda p: p.stat().st_mtime)
        if not candidates:
            return []

        latest = candidates[-1]
        try:
            content = latest.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception as e:
            logger.debug("Could not read log file %s: %s", latest, e)
            return []

        if lines <= 0:
            return []
        return content[-lines:]

    def _analyze_changeset(self, room: 'Room', changeset_path: Path) -> None:
        """Run spot-the-diff against baselines for one changeset image."""
        try:
            from spot_the_diff import analyze_changeset_file
        except Exception as e:
            logger.error(f"Spot-the-diff unavailable: {e}")
            return

        try:
            raw = analyze_changeset_file(
                changeset_path=changeset_path,
                room_id=room.room_id,
                captures_dir=Path("captures"),
                baseline_db=self._baseline_db_path,
                model=self._spot_diff_model,
            )
        except Exception as e:
            logger.error(f"Spot-the-diff failed for {changeset_path.name}: {e}")
            return

        self._spot_diff_logs_dir.mkdir(parents=True, exist_ok=True)
        report_name = f"spot-the-diff-{room.room_id}-{changeset_path.stem}.json"
        report_path = self._spot_diff_logs_dir / report_name
        report_path.write_text(raw + "\n", encoding="utf-8")
        logger.info(f"Spot-the-diff report saved to {report_path}")

    def _start_control_server(self) -> None:
        """Start local FastAPI control endpoint for runtime commands."""
        if self._control_server is not None:
            return

        self._control_server = ControlAPIServer(
            controller=self,
            host=self._control_host,
            port=self._control_port,
        )
        self._control_server.start()
        logger.info("Control API listening at http://%s:%s", self._control_host, self._control_port)

    def _stop_control_server(self) -> None:
        """Stop local FastAPI control endpoint."""
        if self._control_server is None:
            return

        self._control_server.stop(timeout_seconds=2.0)
        self._control_server = None
        logger.info("Control API stopped")

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
                if camera.status_value == "active":
                    camera.output_status()

                # Sleep for poll interval
                import time
                time.sleep(camera.poll_interval)
        except Exception as e:
            logger.error(f"Error in camera loop for {camera.name}: {e}")

    def _run_detector_loop(self, detector: 'PersonDetector') -> None:
        """
        Person detector monitoring loop (runs in separate thread).

        Args:
            detector: PersonDetector instance to monitor
        """
        loop = None
        try:
            # Create event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            # Run async detector
            loop.run_until_complete(self._async_detector_loop(detector))
        except Exception as e:
            logger.error(f"Error in detector loop for {detector.name}: {e}")
        finally:
            if loop is not None:
                loop.close()

    async def _async_detector_loop(self, detector: 'PersonDetector') -> None:
        """
        Async person detector monitoring loop.

        Args:
            detector: PersonDetector instance to monitor
        """
        reconnect_delay = max(0.1, self._detector_reconnect_initial_delay)

        while self._running:
            try:
                await detector.connect()
                await detector.subscribe_to_states()
                reconnect_delay = max(0.1, self._detector_reconnect_initial_delay)

                while self._running:
                    if detector.has_heartbeat_timed_out():
                        sensor_alive = await detector.probe_sensor_alive(self._detector_probe_timeout)
                        if sensor_alive:
                            detector.check_heartbeat_timeout()
                        else:
                            raise ConnectionError("Detector liveness probe failed")
                    disconnected = await detector.wait_for_disconnect(self._detector_poll_interval)
                    if disconnected and self._running:
                        raise ConnectionError("Detector connection dropped")

            except Exception as e:
                if self._running:
                    logger.warning(
                        "Detector %s loop error: %s. Reconnecting in %.1fs",
                        detector.name,
                        e,
                        reconnect_delay,
                    )
            finally:
                try:
                    await detector.disconnect()
                except Exception as e:
                    logger.debug("Detector %s disconnect during retry failed: %s", detector.name, e)

            if self._running:
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, max(reconnect_delay, self._detector_reconnect_max_delay))

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
        self._stop_control_server()

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
