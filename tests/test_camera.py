"""
Test suite for Camera class - Camera behavior and state management.
"""

import pytest
import time
from unittest.mock import Mock, patch
from gel_controller.camera import Camera
from gel_controller.room import Room


class TestCameraInitialization:
    """Test Camera initialization and default properties."""

    def test_camera_initialization(self):
        """Camera starts inactive with default properties."""
        camera = Camera(name="Test Camera", room_id="room1")

        assert camera.get_name() == "Test Camera"
        assert camera.get_room_id() == "room1"
        assert camera.get_state() == "inactive"

    def test_camera_initialization_with_custom_state(self):
        """Camera can be initialized with custom state."""
        camera = Camera(name="Test Camera", room_id="room1", initial_state="active")

        assert camera.get_state() == "active"


class TestCameraProperties:
    """Test Camera property getters and setters."""

    def test_camera_name_getter_setter(self):
        """Get and set camera name."""
        camera = Camera(name="Camera 1", room_id="room1")

        assert camera.get_name() == "Camera 1"

        camera.set_name("Camera 2")
        assert camera.get_name() == "Camera 2"

    def test_camera_state_getter_setter(self):
        """Get and set camera state."""
        camera = Camera(name="Test Camera", room_id="room1")

        assert camera.get_state() == "inactive"

        camera.set_state("active")
        assert camera.get_state() == "active"

        camera.set_state("inactive")
        assert camera.get_state() == "inactive"

    def test_camera_room_id_getter_setter(self):
        """Get and set camera room_id."""
        camera = Camera(name="Test Camera", room_id="room1")

        assert camera.get_room_id() == "room1"

        camera.set_room_id("room2")
        assert camera.get_room_id() == "room2"

    def test_camera_state_invalid_value(self):
        """Setting invalid state should raise error."""
        camera = Camera(name="Test Camera", room_id="room1")

        with pytest.raises(ValueError):
            camera.set_state("invalid_state")


class TestCameraRoomInteraction:
    """Test Camera polling and interaction with Room."""

    def test_camera_polls_room_when_empty(self):
        """Camera checks room state and becomes active when empty."""
        room = Room(room_id="room1", name="Test Room")
        camera = Camera(name="Test Camera", room_id="room1")

        room.set_state("empty")

        # Camera should check room and become active
        camera.check_room_and_update_state(room)

        assert camera.get_state() == "active"

    def test_camera_becomes_inactive_when_room_occupied(self):
        """Camera deactivates when room is occupied."""
        room = Room(room_id="room1", name="Test Room")
        camera = Camera(name="Test Camera", room_id="room1")

        # Camera is active
        camera.set_state("active")

        # Room becomes occupied
        room.set_state("occupied")

        # Camera checks and should become inactive
        camera.check_room_and_update_state(room)

        assert camera.get_state() == "inactive"

    def test_camera_respects_room_forced_inactive(self):
        """Room can force camera off even if room is empty."""
        room = Room(room_id="room1", name="Test Room")
        camera = Camera(name="Test Camera", room_id="room1")

        room.set_state("empty")
        room.add_camera(camera)

        # Room forces camera inactive
        room.set_camera_inactive(camera)

        # Even though room is empty, camera should be inactive
        assert camera.get_state() == "inactive"


class TestCameraOutput:
    """Test Camera output messages."""

    def test_camera_outputs_active_message(self, capsys):
        """Camera outputs 'Camera n active' when active."""
        camera = Camera(name="Camera 1", room_id="room1")
        camera.set_state("active")

        camera.output_status()

        captured = capsys.readouterr()
        assert "Camera 1 active" in captured.out

    def test_camera_does_not_output_when_inactive(self, capsys):
        """No output when camera is inactive."""
        camera = Camera(name="Camera 1", room_id="room1")
        camera.set_state("inactive")

        camera.output_status()

        captured = capsys.readouterr()
        assert "active" not in captured.out


class TestCameraPolling:
    """Test Camera polling behavior."""

    def test_camera_polling_interval(self):
        """Camera polls at correct interval."""
        camera = Camera(name="Test Camera", room_id="room1", poll_interval=1)

        assert camera.get_poll_interval() == 1

        camera.set_poll_interval(5)
        assert camera.get_poll_interval() == 5


class TestMultipleCameras:
    """Test multiple cameras operating independently."""

    def test_multiple_cameras_independent(self):
        """Multiple cameras operate independently."""
        room = Room(room_id="room1", name="Test Room")
        camera1 = Camera(name="Camera 1", room_id="room1")
        camera2 = Camera(name="Camera 2", room_id="room1")

        room.add_camera(camera1)
        room.add_camera(camera2)

        room.set_state("empty")

        # Both cameras should be able to become active
        camera1.check_room_and_update_state(room)
        camera2.check_room_and_update_state(room)

        assert camera1.get_state() == "active"
        assert camera2.get_state() == "active"

        # Room can force one camera inactive while leaving other active
        room.set_camera_inactive(camera1)

        assert camera1.get_state() == "inactive"
        assert camera2.get_state() == "active"
