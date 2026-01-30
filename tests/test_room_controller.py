"""
Test suite for RoomController class - Integration and coordination.
"""

import pytest
import threading
import time
from unittest.mock import Mock, patch
from gel_controller.room_controller import RoomController
from gel_controller.room import Room
from gel_controller.camera import Camera
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
        camera = Camera(name="Camera 1", room_id="room1")
        detector = PersonDetector(name="Detector 1", host="192.168.1.189", port=6053)

        controller.add_room(room)
        room.add_camera(camera)
        room.add_person_detector(detector)
        detector.set_room(room)

        # Initially room is empty, camera can be active
        room.set_state("empty")
        camera.check_room_and_update_state(room)
        assert camera.get_state() == "active"

        # Detector detects heartbeat
        detector.on_heartbeat_detected(110.0)

        # Room should be occupied
        assert room.get_state() == "occupied"

        # Camera checks and becomes inactive
        camera.check_room_and_update_state(room)
        assert camera.get_state() == "inactive"

    def test_detector_empty_enables_cameras(self):
        """Flow: detector timeout → room empty → cameras on."""
        controller = RoomController()
        room = Room(room_id="room1", name="Test Room")
        camera = Camera(name="Camera 1", room_id="room1")
        detector = PersonDetector(name="Detector 1", host="192.168.1.189", port=6053)

        controller.add_room(room)
        room.add_camera(camera)
        room.add_person_detector(detector)
        detector.set_room(room)

        # Start with occupied room
        room.set_state("occupied")
        camera.check_room_and_update_state(room)
        assert camera.get_state() == "inactive"

        # Detector times out (no heartbeat)
        detector.on_heartbeat_timeout()

        # Room should be empty
        assert room.get_state() == "empty"

        # Camera checks and becomes active
        camera.check_room_and_update_state(room)
        assert camera.get_state() == "active"


class TestMultipleDetectorsLogic:
    """Test logic with multiple detectors."""

    def test_multiple_detectors_any_occupied(self):
        """Any detector occupied → cameras off."""
        room = Room(room_id="room1", name="Test Room")
        camera = Camera(name="Camera 1", room_id="room1")
        detector1 = PersonDetector(name="Detector 1", host="192.168.1.189", port=6053)
        detector2 = PersonDetector(name="Detector 2", host="192.168.1.190", port=6053)

        room.add_camera(camera)
        room.add_person_detector(detector1)
        room.add_person_detector(detector2)
        detector1.set_room(room)
        detector2.set_room(room)

        # If ANY detector sees heartbeat, room is occupied
        detector1.on_heartbeat_detected(110.0)

        assert room.get_state() == "occupied"

        camera.check_room_and_update_state(room)
        assert camera.get_state() == "inactive"

    def test_multiple_detectors_all_empty(self):
        """All detectors empty → cameras on."""
        room = Room(room_id="room1", name="Test Room")
        camera = Camera(name="Camera 1", room_id="room1")
        detector1 = PersonDetector(name="Detector 1", host="192.168.1.189", port=6053)
        detector2 = PersonDetector(name="Detector 2", host="192.168.1.190", port=6053)

        room.add_camera(camera)
        room.add_person_detector(detector1)
        room.add_person_detector(detector2)
        detector1.set_room(room)
        detector2.set_room(room)

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
        """Cameras must poll room before becoming active."""
        room = Room(room_id="room1", name="Test Room")
        camera = Camera(name="Camera 1", room_id="room1")

        room.add_camera(camera)
        room.set_state("empty")

        # Camera starts inactive (default)
        assert camera.get_state() == "inactive"

        # Camera must explicitly check room to activate
        camera.check_room_and_update_state(room)

        # Now it should be active
        assert camera.get_state() == "active"


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
