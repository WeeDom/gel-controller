"""
Test suite for Camera class - Camera behavior and state management.
"""

import pytest
import time
from unittest.mock import Mock, patch
from gel_controller.camera import Camera
from gel_controller.camera_state import CameraStatus
from gel_controller.room import Room


class TestCameraInitialization:
    """Test Camera initialization and default properties."""

    def test_camera_initialization(self):
        """Camera starts offline with default properties."""
        camera = Camera(name="Test Camera", room_id="room1")

        assert camera.name == "Test Camera"
        assert camera.room_id == "room1"
        assert camera.status == CameraStatus.OFFLINE

    def test_camera_initialization_with_custom_state(self):
        """Camera can be initialized with custom status."""
        camera = Camera(name="Test Camera", room_id="room1", initial_status=CameraStatus.ACTIVE)

        assert camera.status == CameraStatus.ACTIVE


class TestCameraProperties:
    """Test Camera property getters and setters."""

    def test_camera_name_getter_setter(self):
        """Get and set camera name property."""
        camera = Camera(name="Camera 1", room_id="room1")

        assert camera.name == "Camera 1"

        camera.name = "Camera 2"
        assert camera.name == "Camera 2"

    def test_camera_status_transitions(self):
        """Transition camera status via state machine."""
        camera = Camera(name="Test Camera", room_id="room1", initial_status=CameraStatus.INACTIVE)

        assert camera.status == CameraStatus.INACTIVE

        assert camera.set_status(CameraStatus.ACTIVE) is True
        assert camera.status == CameraStatus.ACTIVE

        assert camera.set_status(CameraStatus.INACTIVE) is True
        assert camera.status == CameraStatus.INACTIVE

    def test_camera_room_id_getter_setter(self):
        """Get and set camera room_id property."""
        camera = Camera(name="Test Camera", room_id="room1")

        assert camera.room_id == "room1"

        camera.room_id = "room2"
        assert camera.room_id == "room2"

    def test_camera_invalid_transition(self):
        """Invalid status transition returns False."""
        camera = Camera(name="Test Camera", room_id="room1")

        assert camera.status == CameraStatus.OFFLINE
        assert camera.set_status(CameraStatus.ACTIVE) is False
        assert camera.status == CameraStatus.OFFLINE


class TestCameraRoomInteraction:
    """Test Camera polling and interaction with Room."""

    def test_camera_polls_room_when_empty(self):
        """Camera checks room state and becomes active when empty."""
        room = Room(room_id="room1", name="Test Room")
        camera = Camera(name="Test Camera", room_id="room1", initial_status=CameraStatus.INACTIVE)

        room.set_state("empty")

        # Camera should check room and become active
        camera.check_room_and_update_state(room)

        assert camera.status == CameraStatus.ACTIVE

    def test_camera_becomes_inactive_when_room_occupied(self):
        """Camera deactivates when room is occupied."""
        room = Room(room_id="room1", name="Test Room")
        camera = Camera(name="Test Camera", room_id="room1", initial_status=CameraStatus.ACTIVE)

        # Room becomes occupied
        room.set_state("occupied")

        # Camera checks and should become inactive
        camera.check_room_and_update_state(room)

        assert camera.status == CameraStatus.INACTIVE

    def test_camera_respects_room_forced_inactive(self):
        """Room can force camera off even if room is empty."""
        room = Room(room_id="room1", name="Test Room")
        camera = Camera(name="Test Camera", room_id="room1", initial_status=CameraStatus.ACTIVE)

        room.set_state("empty")
        room.add_camera(camera)

        # Room forces camera inactive
        room.set_camera_inactive(camera)

        # Even though room is empty, camera should be inactive
        assert camera.status == CameraStatus.INACTIVE


class TestCameraOutput:
    """Test Camera output messages."""

    def test_camera_outputs_active_message(self, capsys):
        """Camera outputs 'Camera n active' when active."""
        camera = Camera(name="Camera 1", room_id="room1", state="active", output_interval=0)

        camera.output_status()

        captured = capsys.readouterr()
        assert "Camera 1 active" in captured.out

    def test_camera_does_not_output_when_inactive(self, capsys):
        """No output when camera is inactive."""
        camera = Camera(name="Camera 1", room_id="room1", state="inactive", output_interval=0)

        camera.output_status()

        captured = capsys.readouterr()
        assert "active" not in captured.out


class TestCameraPolling:
    """Test Camera polling behavior."""

    def test_camera_polling_interval(self):
        """Camera polls at correct interval."""
        camera = Camera(name="Test Camera", room_id="room1", poll_interval=1)

        assert camera.poll_interval == 1

        camera.poll_interval = 5
        assert camera.poll_interval == 5


class TestMultipleCameras:
    """Test multiple cameras operating independently."""

    def test_multiple_cameras_independent(self):
        """Multiple cameras operate independently."""
        room = Room(room_id="room1", name="Test Room")
        camera1 = Camera(name="Camera 1", room_id="room1", initial_status=CameraStatus.INACTIVE)
        camera2 = Camera(name="Camera 2", room_id="room1", initial_status=CameraStatus.INACTIVE)

        room.add_camera(camera1)
        room.add_camera(camera2)

        room.set_state("empty")

        # Both cameras should be able to become active
        camera1.check_room_and_update_state(room)
        camera2.check_room_and_update_state(room)

        assert camera1.status == CameraStatus.ACTIVE
        assert camera2.status == CameraStatus.ACTIVE

        # Room can force one camera inactive while leaving other active
        room.set_camera_inactive(camera1)

        assert camera1.status == CameraStatus.INACTIVE
        assert camera2.status == CameraStatus.ACTIVE
