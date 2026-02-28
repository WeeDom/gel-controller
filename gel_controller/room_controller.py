"""
RoomController - Orchestrates multiple rooms with cameras and person detectors.
"""

import asyncio
import threading
import json
import sqlite3
import os
from datetime import datetime
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import List, Dict, TYPE_CHECKING, Optional
import logging

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
        self._control_server: Optional[ThreadingHTTPServer] = None
        self._control_thread: Optional[threading.Thread] = None
        self._control_host = "127.0.0.1"
        self._control_port = 8765
        self._baseline_db_path = Path("logs") / "baselines.db"
        self._spot_diff_enabled = os.getenv("ENABLE_SPOT_THE_DIFF", "1").lower() not in {"0", "false", "no"}
        self._spot_diff_model = os.getenv("SPOT_THE_DIFF_MODEL", "claude-opus-4-5-20251101")
        self._spot_diff_logs_dir = Path("logs") / "spot_the_diff"
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
        """Start local HTTP control endpoint for runtime commands."""
        if self._control_server is not None:
            return

        controller = self

        class _ControlHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                request_path = self.path.split("?", 1)[0]
                if request_path not in {"/capture-baseline", "/analyze-latest"}:
                    self.send_response(404)
                    self.end_headers()
                    return

                content_length = int(self.headers.get("Content-Length", "0"))
                payload = {}
                if content_length > 0:
                    raw = self.rfile.read(content_length)
                    try:
                        payload = json.loads(raw.decode("utf-8"))
                    except json.JSONDecodeError:
                        self.send_response(400)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"ok": False, "error": "Invalid JSON"}).encode("utf-8"))
                        return

                room_id = payload.get("room_id")
                if request_path == "/capture-baseline":
                    result = controller.capture_baseline(room_id=room_id)
                else:
                    result = controller.analyze_latest(room_id=room_id)

                status = 200 if result.get("ok") else 404
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(result).encode("utf-8"))

            def log_message(self, format, *args):
                logger.debug("Control API: " + format, *args)

        self._control_server = ThreadingHTTPServer((self._control_host, self._control_port), _ControlHandler)
        self._control_thread = threading.Thread(
            target=self._control_server.serve_forever,
            name="Controller-Control-API",
            daemon=True,
        )
        self._control_thread.start()
        logger.info("Control API listening at http://%s:%s", self._control_host, self._control_port)

    def _stop_control_server(self) -> None:
        """Stop local HTTP control endpoint."""
        if self._control_server is None:
            return

        self._control_server.shutdown()
        self._control_server.server_close()
        if self._control_thread is not None:
            self._control_thread.join(timeout=2.0)

        self._control_server = None
        self._control_thread = None
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
