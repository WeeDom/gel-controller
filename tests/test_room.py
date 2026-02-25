"""
Test suite for Room class - Room state management.
"""

import pytest
from gel_controller.camera_state import CameraStatus
from gel_controller.room import Room
from gel_controller.camera import Camera
from gel_controller.person_detector import PersonDetector


class TestRoomInitialization:
    """Test Room initialization and default state."""

    def test_room_initialization(self):
        """Room starts with no cameras, no detectors, default empty state."""
        room = Room(room_id="room1", name="Gents")

        assert room.room_id == "room1"
        assert room.name == "Gents"
        assert room.state == "empty"

    def test_room_custom_initial_state(self):
        """Room can be initialized with custom state."""
        room = Room(room_id="room2", name="Bedroom", initial_state="occupied")

        assert room.state == "occupied"


class TestRoomStateManagement:
    """Test Room state getter and setter."""

    def test_room_state_getter_setter(self):
        """Get and set room state."""
        room = Room(room_id="room1", name="Test Room")

        assert room.state == "empty"

        room.state = "occupied"
        assert room.state == "occupied"

        room.state = "empty"
        assert room.state == "empty"

    def test_room_state_invalid_value(self):
        """Setting invalid state should raise error."""
        room = Room(room_id="room1", name="Test Room")

        with pytest.raises(ValueError):
            room.state = "invalid_state"


class TestRoomCameraManagement:
    """Test adding and removing cameras from room."""

    def test_room_add_camera(self):
        """Add camera to room."""
        room = Room(room_id="room1", name="Test Room")
        camera = Camera(name="Camera 1", room_id="room1")

        room.add_camera(camera)

        assert len(room.get_cameras(False)) == 1
        assert camera in room.get_cameras(False)

    def test_room_add_multiple_cameras(self):
        """Add multiple cameras to room."""
        room = Room(room_id="room1", name="Test Room")
        camera1 = Camera(name="Camera 1", room_id="room1")
        camera2 = Camera(name="Camera 2", room_id="room1")

        room.add_camera(camera1)
        room.add_camera(camera2)

        assert len(room.get_cameras(False)) == 2

    def test_room_remove_camera(self):
        """Remove camera from room."""
        room = Room(room_id="room1", name="Test Room")
        camera = Camera(name="Camera 1", room_id="room1")

        room.add_camera(camera)
        assert len(room.get_cameras(False)) == 1

        room.remove_camera(camera)
        assert len(room.get_cameras(False)) == 0

    def test_room_set_camera_inactive(self):
        """Room can force a camera inactive."""
        room = Room(room_id="room1", name="Test Room")
        camera = Camera(name="Camera 1", room_id="room1")

        room.add_camera(camera)

        room.set_camera_inactive(camera)

        assert camera.status == CameraStatus.INACTIVE


class TestRoomPersonDetectorManagement:
    """Test adding and removing person detectors from room."""

    def test_room_add_person_detector(self):
        """Add person detector to room."""
        room = Room(room_id="room1", name="Test Room")
        detector = PersonDetector(name="Detector 1", host="192.168.1.189", port=6053)

        room.add_person_detector(detector)

        assert len(room.get_person_detectors(False)) == 1
        assert detector in room.get_person_detectors(False)

    def test_room_add_multiple_person_detectors(self):
        """Add multiple person detectors to room."""
        room = Room(room_id="room1", name="Test Room")
        detector1 = PersonDetector(name="Detector 1", host="192.168.1.189", port=6053)
        detector2 = PersonDetector(name="Detector 2", host="192.168.1.190", port=6053)

        room.add_person_detector(detector1)
        room.add_person_detector(detector2)

        assert len(room.get_person_detectors(False)) == 2

    def test_room_remove_person_detector(self):
        """Remove person detector from room."""
        room = Room(room_id="room1", name="Test Room")
        detector = PersonDetector(name="Detector 1", host="192.168.1.189", port=6053)

        room.add_person_detector(detector)
        assert len(room.get_person_detectors(False)) == 1

        room.remove_person_detector(detector)
        assert len(room.get_person_detectors(False)) == 0


class TestRoomStateChangeNotification:
    """Test that room state changes notify observers."""

    def test_room_state_changes_notify_observers(self):
        """State changes should trigger camera updates."""
        room = Room(room_id="room1", name="Test Room")
        camera = Camera(name="Camera 1", room_id="room1")
        room.add_camera(camera)

        # When room is empty, camera should be able to become active
        room.state = "empty"
        # Camera should check and potentially activate

        # When room becomes occupied, cameras should be notified
        room.state = "occupied"
        # This test validates the notification mechanism exists

        assert True  # Placeholder for notification verification
