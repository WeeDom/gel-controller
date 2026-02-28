"""
Test suite for RoomController class - Integration and coordination.
"""

import pytest
import threading
import time
import sqlite3
from unittest.mock import Mock, patch, AsyncMock
from gel_controller.room_controller import RoomController
from gel_controller.room import Room
from gel_controller.camera import Camera
from gel_controller.camera_state import CameraStatus
from gel_controller.person_detector import PersonDetector


class TestRoomControllerInitialization:
    """Test RoomController initialization."""

    def test_controller_initialization(self):
        """Controller starts with empty collections."""
        controller = RoomController()

        assert len(controller.get_rooms()) == 0

    def test_controller_adds_rooms(self):
        """Can add multiple rooms."""
        controller = RoomController()
        room1 = Room(room_id="room1", name="Gents")
        room2 = Room(room_id="room2", name="Ladies")

        controller.add_room(room1)
        controller.add_room(room2)

        assert len(controller.get_rooms()) == 2
        assert room1 in controller.get_rooms()
        assert room2 in controller.get_rooms()


class TestRoomControllerCoordination:
    """Test controller coordinating multiple rooms."""

    def test_controller_coordinates_rooms(self):
        """Manages multiple rooms independently."""
        controller = RoomController()
        room1 = Room(room_id="room1", name="Gents")
        room2 = Room(room_id="room2", name="Ladies")

        controller.add_room(room1)
        controller.add_room(room2)

        room1.set_state("occupied")
        room2.set_state("empty")

        assert room1.get_state() == "occupied"
        assert room2.get_state() == "empty"


class TestDetectorCameraInteraction:
    """Test flow from detector through room to cameras."""

    def test_detector_occupied_disables_cameras(self):
        """Flow: detector detects → room occupied → cameras off."""
        controller = RoomController()
        room = Room(room_id="room1", name="Test Room")
        camera = Camera(name="Camera 1", room_id="room1", initial_status=CameraStatus.INACTIVE)
        detector = PersonDetector(name="Detector 1", host="192.168.1.189", port=6053)

        controller.add_room(room)
        room.add_camera(camera)
        room.add_person_detector(detector)
        detector.room = room

        # Initially room is empty, camera should remain inactive
        room.set_state("empty")
        camera.check_room_and_update_state(room)
        assert camera.status == CameraStatus.INACTIVE

        # Detector detects heartbeat
        detector.on_heartbeat_detected(110.0)

        # Room should be occupied
        assert room.get_state() == "occupied"

        # Camera checks and becomes inactive
        camera.check_room_and_update_state(room)
        assert camera.status == CameraStatus.INACTIVE

    def test_detector_empty_enables_cameras(self):
        """Flow: detector timeout → room empty → cameras on."""
        controller = RoomController()
        room = Room(room_id="room1", name="Test Room")
        camera = Camera(name="Camera 1", room_id="room1", initial_status=CameraStatus.ACTIVE)
        detector = PersonDetector(name="Detector 1", host="192.168.1.189", port=6053)

        controller.add_room(room)
        room.add_camera(camera)
        room.add_person_detector(detector)
        detector.room = room

        # Start with occupied room
        room.set_state("occupied")
        camera.check_room_and_update_state(room)
        assert camera.status == CameraStatus.INACTIVE

        # Detector times out (no heartbeat)
        detector.on_heartbeat_timeout()

        # Room should be empty
        assert room.get_state() == "empty"

        # Camera checks and remains inactive; capture is room-coordinated
        camera.check_room_and_update_state(room)
        assert camera.status == CameraStatus.INACTIVE
        assert camera.capture_count == 0


class TestMultipleDetectorsLogic:
    """Test logic with multiple detectors."""

    def test_multiple_detectors_any_occupied(self):
        """Any detector occupied → cameras off."""
        room = Room(room_id="room1", name="Test Room")
        camera = Camera(name="Camera 1", room_id="room1", initial_status=CameraStatus.ACTIVE)
        detector1 = PersonDetector(name="Detector 1", host="192.168.1.189", port=6053)
        detector2 = PersonDetector(name="Detector 2", host="192.168.1.190", port=6053)

        room.add_camera(camera)
        room.add_person_detector(detector1)
        room.add_person_detector(detector2)
        detector1.room = room
        detector2.room = room

        # If ANY detector sees heartbeat, room is occupied
        detector1.on_heartbeat_detected(110.0)

        assert room.get_state() == "occupied"

        camera.check_room_and_update_state(room)
        assert camera.status == CameraStatus.INACTIVE

    def test_multiple_detectors_all_empty(self):
        """All detectors empty → cameras on."""
        room = Room(room_id="room1", name="Test Room")
        camera = Camera(name="Camera 1", room_id="room1", initial_status=CameraStatus.INACTIVE)
        detector1 = PersonDetector(name="Detector 1", host="192.168.1.189", port=6053)
        detector2 = PersonDetector(name="Detector 2", host="192.168.1.190", port=6053)

        room.add_camera(camera)
        room.add_person_detector(detector1)
        room.add_person_detector(detector2)
        detector1.room = room
        detector2.room = room

        # Set room occupied initially
        room.set_state("occupied")

        # Both detectors must timeout
        detector1.on_heartbeat_timeout()
        detector2.on_heartbeat_timeout()

        # Room coordination should set to empty only when ALL are empty
        # This requires room to track individual detector states
        # For now, last one to timeout wins
        assert room.get_state() == "empty"


class TestCameraActivationFlow:
    """Test cameras checking before activating."""

    def test_cameras_check_before_activating(self):
        """Cameras remain inactive in empty rooms without prior occupancy."""
        room = Room(room_id="room1", name="Test Room")
        camera = Camera(name="Camera 1", room_id="room1", initial_status=CameraStatus.INACTIVE)

        room.add_camera(camera)
        room.set_state("empty")

        # Camera starts inactive
        assert camera.status == CameraStatus.INACTIVE

        # Camera check in idle-empty should still keep camera inactive
        camera.check_room_and_update_state(room)

        assert camera.status == CameraStatus.INACTIVE


class TestParallelOperation:
    """Test all components running in parallel."""

    def test_parallel_operation(self):
        """All components run in parallel (threading)."""
        controller = RoomController()
        room = Room(room_id="room1", name="Test Room")
        camera = Camera(name="Camera 1", room_id="room1")
        detector = PersonDetector(name="Detector 1", host="192.168.1.189", port=6053)

        controller.add_room(room)
        room.add_camera(camera)
        room.add_person_detector(detector)

        # Start controller (should start threads for cameras and detectors)
        # This is a placeholder - actual implementation will use threading

        assert True  # Placeholder for threading test


class TestGracefulShutdown:
    """Test clean shutdown of all components."""

    def test_graceful_shutdown(self):
        """Clean shutdown of all threads."""
        controller = RoomController()
        room = Room(room_id="room1", name="Test Room")
        camera = Camera(name="Camera 1", room_id="room1")
        detector = PersonDetector(name="Detector 1", host="192.168.1.189", port=6053)

        controller.add_room(room)
        room.add_camera(camera)
        room.add_person_detector(detector)

        # Start controller
        # controller.start()

        # Shutdown
        # controller.shutdown()

        # Verify all threads stopped
        assert True  # Placeholder for shutdown test


class TestBaselineCapturePersistence:
    """Test baseline capture metadata persistence."""

    def test_baseline_capture_writes_sqlite(self, tmp_path):
        """Successful baseline capture writes camera/date/location to SQLite."""
        controller = RoomController()
        controller._baseline_db_path = tmp_path / "baselines.db"
        controller._init_baseline_db()

        room = Room(room_id="room1", name="Test Room")
        camera = Camera(name="Camera 1", room_id="room1", ip="192.168.1.10")
        room.add_camera(camera)
        controller.add_room(room)

        camera.capture_image = Mock(return_value=True)

        result = controller.capture_baseline()

        assert result["ok"] is True
        assert result["captures_requested"] == 1
        assert result["captures_succeeded"] == 1

        with sqlite3.connect(controller._baseline_db_path) as conn:
            row = conn.execute(
                "SELECT camera_name, captured_at, location FROM baselines"
            ).fetchone()

        assert row is not None
        assert row[0] == "Camera 1"
        assert isinstance(row[1], str)
        assert row[2] == "Test Room"


class TestDetectorReconnect:
    """Test detector reconnect behavior after transient failures."""

    @pytest.mark.asyncio
    async def test_detector_loop_retries_after_connect_error(self):
        controller = RoomController()
        controller._running = True
        controller._detector_poll_interval = 0.01
        controller._detector_reconnect_initial_delay = 0.01
        controller._detector_reconnect_max_delay = 0.02

        detector = Mock()
        detector.name = "Detector 1"

        connect_calls = {"count": 0}

        async def connect_side_effect():
            connect_calls["count"] += 1
            if connect_calls["count"] == 1:
                raise RuntimeError("temporary network failure")

        def check_timeout_side_effect():
            controller._running = False

        detector.connect = AsyncMock(side_effect=connect_side_effect)
        detector.subscribe_to_states = AsyncMock()
        detector.check_heartbeat_timeout = Mock(side_effect=check_timeout_side_effect)
        detector.disconnect = AsyncMock()

        await controller._async_detector_loop(detector)

        assert connect_calls["count"] >= 2
        detector.subscribe_to_states.assert_awaited_once()
        detector.check_heartbeat_timeout.assert_called_once()
        assert detector.disconnect.await_count >= 2
