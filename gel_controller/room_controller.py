"""
RoomController - Orchestrates multiple rooms with cameras and person detectors.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import re
import threading
import sqlite3
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Dict, TYPE_CHECKING, Optional
import logging
from .control_api import ControlAPIServer

_CAPTURE_RE = re.compile(
    r'^capture-(?P<room_id>[^-]+)-(?P<camera_name>.+)-(?P<timestamp>\d{8}_\d{6}(?:_\d+)?)\.jpe?g$',
    re.IGNORECASE,
)
_BASELINE_RE = re.compile(
    r'^baseline-(?P<room_id>[^-]+)-(?P<camera_name>.+)-(?P<timestamp>\d{8}_\d{6}(?:_\d+)?)\.jpe?g$',
    re.IGNORECASE,
)
_SAFE_IMAGE_RE = re.compile(
    r'^(baseline|capture)-[A-Za-z0-9]+-[A-Za-z0-9]+-\d{8}_\d{6}(?:_\d+)?\.jpe?g$',
    re.IGNORECASE,
)

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
        self._control_host = os.getenv("GEL_CONTROL_HOST", "0.0.0.0")
        self._control_port = 8765
        self._baseline_db_path = Path("logs") / "baselines.db"
        self._spot_diff_enabled = os.getenv("ENABLE_SPOT_THE_DIFF", "1").lower() not in {"0", "false", "no"}
        self._spot_diff_model = os.getenv("SPOT_THE_DIFF_MODEL", "claude-opus-4-5-20251101")
        self._spot_diff_logs_dir = Path("logs") / "spot_the_diff"
        self._detector_poll_interval = float(os.getenv("DETECTOR_POLL_INTERVAL", "1.0"))
        self._detector_probe_timeout = float(os.getenv("DETECTOR_PROBE_TIMEOUT", "2.0"))
        self._detector_reconnect_initial_delay = float(os.getenv("DETECTOR_RECONNECT_INITIAL_DELAY", "2.0"))
        self._detector_reconnect_max_delay = float(os.getenv("DETECTOR_RECONNECT_MAX_DELAY", "60.0"))
        self._camera_discovery_interval = float(os.getenv("CAMERA_DISCOVERY_INTERVAL", "300"))  # seconds
        self._running_camera_keys: set = set()  # MAC or IP of cameras with live threads
        self._running_camera_keys_lock = threading.Lock()
        self._control_job_workers = max(1, int(os.getenv("GEL_CONTROL_JOB_WORKERS", "1")))
        self._control_jobs_executor = ThreadPoolExecutor(
            max_workers=self._control_job_workers,
            thread_name_prefix="ControlJob",
        )
        self._control_jobs: Dict[str, Dict[str, object]] = {}
        self._control_jobs_lock = threading.Lock()
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
            room.set_vacated_callback(self._on_room_vacated)
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
            room.set_vacated_callback(None)
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
                self._start_camera_thread(camera, room)

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

        # Periodic camera rediscovery
        discovery_thread = threading.Thread(
            target=self._run_discovery_loop,
            name="CameraDiscovery",
            daemon=True,
        )
        discovery_thread.start()
        self._threads.append(discovery_thread)

        logger.info(f"Started {len(self._threads)} thread(s)")

    def capture_baseline(self, room_id: Optional[str] = None) -> Dict[str, object]:
        """Backward-compatible blocking baseline capture."""
        return self._capture_baseline_sync(room_id=room_id)

    def enqueue_capture_baseline(self, room_id: Optional[str] = None) -> Dict[str, object]:
        """Queue baseline capture as a background job and return immediately."""
        job_id = str(uuid.uuid4())
        created_at = datetime.now().isoformat()
        job = {
            "ok": True,
            "job_id": job_id,
            "job_type": "capture_baseline",
            "status": "queued",
            "room_id": room_id,
            "created_at": created_at,
            "started_at": None,
            "completed_at": None,
            "result": None,
            "error": None,
        }
        with self._control_jobs_lock:
            self._control_jobs[job_id] = job

        self._control_jobs_executor.submit(self._run_capture_baseline_job, job_id, room_id)
        return dict(job)

    def get_control_job(self, job_id: str) -> Dict[str, object]:
        """Return control job status/result by job id."""
        with self._control_jobs_lock:
            job = self._control_jobs.get(job_id)
            if job is None:
                return {"ok": False, "error": "job_not_found", "job_id": job_id}
            return {"ok": True, **dict(job)}

    def _run_capture_baseline_job(self, job_id: str, room_id: Optional[str]) -> None:
        started_at = datetime.now().isoformat()
        with self._control_jobs_lock:
            job = self._control_jobs.get(job_id)
            if job is None:
                return
            job["status"] = "running"
            job["started_at"] = started_at

        try:
            result = self._capture_baseline_sync(room_id=room_id)
            completed_at = datetime.now().isoformat()
            with self._control_jobs_lock:
                job = self._control_jobs.get(job_id)
                if job is None:
                    return
                job["status"] = "completed" if result.get("ok") else "failed"
                job["completed_at"] = completed_at
                job["result"] = result
        except Exception as exc:
            completed_at = datetime.now().isoformat()
            with self._control_jobs_lock:
                job = self._control_jobs.get(job_id)
                if job is None:
                    return
                job["status"] = "failed"
                job["completed_at"] = completed_at
                job["error"] = str(exc)

    def _capture_baseline_sync(self, room_id: Optional[str] = None) -> Dict[str, object]:
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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS occupancy_cycles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cycle_id TEXT NOT NULL UNIQUE,
                    room_id TEXT NOT NULL,
                    room_name TEXT NOT NULL,
                    vacated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cycle_captures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cycle_id TEXT NOT NULL REFERENCES occupancy_cycles(cycle_id),
                    filename TEXT NOT NULL,
                    camera_name TEXT NOT NULL,
                    captured_at TEXT NOT NULL
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
        """Record cycle captures and kick off a single spot-the-diff call for all cameras."""
        cycle_id = getattr(room, '_current_cycle_id', None)
        event_number = getattr(room, '_current_event_number', None)
        if cycle_id and captured_files:
            self._record_cycle_captures(cycle_id, captured_files)

        if not self._spot_diff_enabled or not captured_files:
            return

        thread = threading.Thread(
            target=self._analyze_event,
            args=(room, event_number, list(captured_files)),
            name=f"SpotDiff-{room.room_id}-event{event_number}",
            daemon=True,
        )
        thread.start()

    def _on_room_vacated(self, room: 'Room') -> None:
        """Open an occupancy cycle record when a room transitions occupied → empty."""
        cycle_id = str(uuid.uuid4())
        vacated_at = datetime.now().isoformat()
        with sqlite3.connect(self._baseline_db_path) as conn:
            cur = conn.execute(
                "INSERT INTO occupancy_cycles (cycle_id, room_id, room_name, vacated_at) VALUES (?, ?, ?, ?)",
                (cycle_id, room.room_id, room.name, vacated_at),
            )
            conn.commit()
            event_number = cur.lastrowid
        room._current_cycle_id = cycle_id
        room._current_event_number = event_number
        logger.info(f"Opened occupancy cycle {cycle_id} (event #{event_number}) for room {room.room_id}")

    def _record_cycle_captures(self, cycle_id: str, captured_files: List[Path]) -> None:
        """Record capture filenames against an occupancy cycle."""
        captured_at = datetime.now().isoformat()
        with sqlite3.connect(self._baseline_db_path) as conn:
            for path in captured_files:
                m = _CAPTURE_RE.match(path.name)
                camera_name = m.group("camera_name") if m else path.stem
                conn.execute(
                    "INSERT INTO cycle_captures (cycle_id, filename, camera_name, captured_at) VALUES (?, ?, ?, ?)",
                    (cycle_id, path.name, camera_name, captured_at),
                )
            conn.commit()
        logger.info(f"Recorded {len(captured_files)} capture(s) for cycle {cycle_id}")

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

            # Pick latest capture per camera so all cameras are included
            by_camera: Dict[str, Path] = {}
            for path in candidates:
                m = _CAPTURE_RE.match(path.name)
                cam = m.group("camera_name") if m else path.stem
                existing = by_camera.get(cam)
                if existing is None or path.stat().st_mtime > existing.stat().st_mtime:
                    by_camera[cam] = path

            all_latest = list(by_camera.values())
            thread = threading.Thread(
                target=self._analyze_event,
                args=(room, None, all_latest),
                name=f"SpotDiffManual-{room.room_id}",
                daemon=True,
            )
            thread.start()
            queued += 1
            queued_files.extend(str(p) for p in all_latest)

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

    def _analyze_event(self, room: 'Room', event_number: Optional[int], captured_files: List[Path]) -> None:
        """Run one composite spot-the-diff analysis for all cameras in one occupancy event."""
        try:
            from spot_the_diff import analyze_changeset_set
        except Exception as e:
            logger.error(f"Spot-the-diff unavailable: {e}")
            return

        try:
            raw = analyze_changeset_set(
                changeset_paths=list(captured_files),
                room_id=room.room_id,
                captures_dir=Path("captures"),
                baseline_db=self._baseline_db_path,
                model=self._spot_diff_model,
            )
        except Exception as e:
            logger.error(
                f"Spot-the-diff failed for event #{event_number} room {room.room_id}: {e}"
            )
            return

        try:
            combined = json.loads(raw)
        except Exception:
            logger.error(
                "Spot-the-diff returned invalid JSON for event #%s room %s",
                event_number,
                room.room_id,
            )
            return

        if not isinstance(combined, dict):
            logger.error(
                "Spot-the-diff returned non-object JSON for event #%s room %s",
                event_number,
                room.room_id,
            )
            return

        if "summary" not in combined and isinstance(combined.get("full_report"), str):
            combined["summary"] = combined["full_report"]

        if not isinstance(combined.get("recommended_actions"), list):
            combined["recommended_actions"] = []

        if not isinstance(combined.get("changesets"), list):
            combined["changesets"] = []

        if not isinstance(combined.get("per_camera"), list):
            combined["per_camera"] = [
                {
                    "camera_name": item.get("camera_name", ""),
                    "room_id": item.get("room_id", room.room_id),
                    "status": item.get("status", "uncertain"),
                    "differences": item.get("differences", []),
                    "confidence": item.get("confidence", 0.0),
                    "baseline_file": item.get("baseline_file"),
                    "capture_file": item.get("capture_file"),
                }
                for item in combined["changesets"]
                if isinstance(item, dict)
            ]

        if combined.get("person_detected"):
            logger.warning(
                "Spot-the-diff processing stopped for event #%s room %s due to person detection",
                event_number,
                room.room_id,
            )

        self._spot_diff_logs_dir.mkdir(parents=True, exist_ok=True)
        if event_number is not None:
            label = f"event{event_number:05d}"
        else:
            label = f"manual-{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        report_name = f"spot-the-diff-{room.room_id}-{label}.json"
        report_path = self._spot_diff_logs_dir / report_name
        report_path.write_text(json.dumps(combined, indent=2) + "\n", encoding="utf-8")
        logger.info(f"Spot-the-diff report saved to {report_path}")

    def list_events(self, room_id: Optional[str] = None) -> Dict[str, object]:
        """Return occupancy cycles grouped by room, each with per-camera captures and reports."""
        _VERDICT_ORDER = ["significant_change", "major_change", "minor_change", "no_change"]

        # Build latest-baseline lookup: (room_id, camera_name) → filename
        captures_dir = Path("captures")
        latest_baselines: Dict[tuple, str] = {}
        for bpath in captures_dir.glob("baseline-*.jp*"):
            m = _BASELINE_RE.match(bpath.name)
            if not m:
                continue
            key = (m.group("room_id"), m.group("camera_name"))
            existing = latest_baselines.get(key)
            if existing is None:
                latest_baselines[key] = bpath.name
            else:
                ex_m = _BASELINE_RE.match(existing)
                if ex_m and m.group("timestamp") > ex_m.group("timestamp"):
                    latest_baselines[key] = bpath.name

        if not self._baseline_db_path.exists():
            return {"ok": True, "rooms": {}}

        with sqlite3.connect(self._baseline_db_path) as conn:
            conn.row_factory = sqlite3.Row

            if room_id is not None:
                cycles = conn.execute(
                    "SELECT * FROM occupancy_cycles WHERE room_id = ? ORDER BY vacated_at DESC",
                    (room_id,),
                ).fetchall()
            else:
                cycles = conn.execute(
                    "SELECT * FROM occupancy_cycles ORDER BY vacated_at DESC"
                ).fetchall()

            rooms: Dict[str, list] = {}
            for cycle in cycles:
                cycle_id = cycle["cycle_id"]
                rid = cycle["room_id"]
                event_number: int = cycle["id"]

                # Load event-level report (new multi-camera style)
                event_report_path = (
                    self._spot_diff_logs_dir / f"spot-the-diff-{rid}-event{event_number:05d}.json"
                )
                event_report = None
                if event_report_path.exists():
                    try:
                        event_report = json.loads(event_report_path.read_text(encoding="utf-8"))
                    except Exception:
                        pass

                # Build per-camera lookup from event report
                per_cam_by_name: Dict[str, dict] = {}
                if event_report and isinstance(event_report.get("per_camera"), list):
                    for pc in event_report["per_camera"]:
                        name = pc.get("camera_name", "")
                        if name:
                            per_cam_by_name[name] = pc

                capture_rows = conn.execute(
                    "SELECT * FROM cycle_captures WHERE cycle_id = ? ORDER BY captured_at",
                    (cycle_id,),
                ).fetchall()

                captures_out = []
                verdicts_seen: List[str] = []
                for cap in capture_rows:
                    filename = cap["filename"]
                    cam_name = cap["camera_name"]

                    # Prefer event-level report; fall back to legacy per-capture report
                    report = None
                    if event_report:
                        per_cam = per_cam_by_name.get(cam_name)
                        if per_cam:
                            # Synthesise a per-camera-shaped report for the table row
                            report = {
                                "overall_verdict": per_cam.get("status"),
                                "confidence": per_cam.get("camera_confidence"),
                                "summary": event_report.get("summary", ""),
                                "viewing_angle": per_cam.get("viewing_angle"),
                                "per_camera": [per_cam],
                                "cross_camera_validation": event_report.get("cross_camera_validation"),
                                "recommended_actions": event_report.get("recommended_actions", []),
                            }
                    if report is None:
                        stem = Path(filename).stem
                        old_path = self._spot_diff_logs_dir / f"spot-the-diff-{rid}-{stem}.json"
                        if old_path.exists():
                            try:
                                report = json.loads(old_path.read_text(encoding="utf-8"))
                            except Exception:
                                pass

                    v = (report or {}).get("overall_verdict", "")
                    if v:
                        verdicts_seen.append(v)

                    captures_out.append({
                        "filename": filename,
                        "camera_name": cam_name,
                        "captured_at": cap["captured_at"],
                        "has_report": report is not None,
                        "report": report,
                        "event_report": event_report,
                        "baseline_file": latest_baselines.get((rid, cam_name)),
                    })

                if event_report:
                    overall_verdict = event_report.get("overall_verdict")
                else:
                    overall_verdict = next(
                        (v for v in _VERDICT_ORDER if v in verdicts_seen),
                        verdicts_seen[0] if verdicts_seen else None,
                    )

                rooms.setdefault(rid, []).append({
                    "cycle_id": cycle_id,
                    "event_number": event_number,
                    "room_id": rid,
                    "room_name": cycle["room_name"],
                    "vacated_at": cycle["vacated_at"],
                    "overall_verdict": overall_verdict,
                    "event_report": event_report,
                    "captures": captures_out,
                })

        return {"ok": True, "rooms": rooms}

    def on_breakbeam_trigger(self, sensor_id: str, room_id: str, beam_broken: bool) -> Dict[str, object]:
        """Handle a break-beam sensor event from a LAN device.

        beam_broken=True  → beam interrupted, person crossing threshold → room occupied.
        beam_broken=False → beam restored (person cleared threshold).
        """
        matched_rooms = [
            r for r in self._rooms
            if room_id in (r.room_id, r.name, "*") or not room_id
        ]
        if not matched_rooms:
            logger.warning("Breakbeam %s: no room matched room_id=%r", sensor_id, room_id)
            return {"ok": False, "error": f"no room matched room_id={room_id!r}"}

        for room in matched_rooms:
            if beam_broken:
                logger.info("🚨 Breakbeam %s: beam BROKEN → room %s occupied", sensor_id, room.room_id)
                room.state = "occupied"
            else:
                logger.info("✅ Breakbeam %s: beam CLEAR (room %s — no state change)", sensor_id, room.room_id)

        return {"ok": True, "beam_broken": beam_broken, "rooms_updated": [r.room_id for r in matched_rooms]}

    def get_image_bytes(self, filename: str) -> Optional[bytes]:
        """Return raw JPEG bytes for a capture or baseline file, or None if not found/invalid."""
        if not _SAFE_IMAGE_RE.match(filename):
            return None
        path = Path("captures") / filename
        if not path.exists():
            return None
        return path.read_bytes()

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

    def _camera_key(self, camera: 'Camera') -> str:
        """Stable identifier for a camera: prefer MAC, fall back to IP."""
        return camera.mac if camera.mac else (camera.ip or camera.name)

    def _start_camera_thread(self, camera: 'Camera', room: 'Room') -> None:
        """Start a monitoring thread for a camera if one isn't already running."""
        key = self._camera_key(camera)
        with self._running_camera_keys_lock:
            if key in self._running_camera_keys:
                return
            self._running_camera_keys.add(key)

        thread = threading.Thread(
            target=self._run_camera_loop,
            args=(camera, room),
            name=f"Camera-{camera.name}",
            daemon=True,
        )
        thread.start()
        self._threads.append(thread)
        logger.info(f"Started thread for camera: {camera.name} (key={key})")

    def _run_discovery_loop(self) -> None:
        """Periodically rediscover cameras in all rooms and start threads for new ones."""
        interval = self._camera_discovery_interval
        logger.info(f"Camera discovery loop started (interval={interval:.0f}s)")
        while self._running:
            # Sleep first — initial discovery already done in start()
            for _ in range(int(interval)):
                if not self._running:
                    return
                time.sleep(1)

            logger.info("Running periodic camera rediscovery…")
            for room in self._rooms:
                try:
                    cameras = room.get_cameras(search_network=True)
                except Exception as exc:
                    logger.warning(f"Camera rediscovery failed for room {room.room_id}: {exc}")
                    continue

                for camera in cameras:
                    self._start_camera_thread(camera, room)

    def _run_camera_loop(self, camera: 'Camera', room: 'Room') -> None:
        """
        Camera monitoring loop (runs in separate thread).

        Args:
            camera: Camera instance to monitor
            room: Room instance the camera belongs to
        """
        key = self._camera_key(camera)
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
        finally:
            with self._running_camera_keys_lock:
                self._running_camera_keys.discard(key)
            logger.info(f"Camera thread exited for {camera.name} (key={key})")

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
        self._control_jobs_executor.shutdown(wait=False, cancel_futures=True)

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
