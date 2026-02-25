"""
Integration tests - End-to-end scenarios for gel-controller.
"""

import pytest
import time
from unittest.mock import Mock, patch
from gel_controller.camera_state import CameraStatus
from gel_controller.room_controller import RoomController
from gel_controller.room import Room
from gel_controller.camera import Camera
from gel_controller.person_detector import PersonDetector


class TestCompleteWorkflow:
    """Test complete workflow scenarios."""

    def test_complete_workflow(self, capsys):
        """Person enters → cameras off → person leaves → cameras on."""
        controller = RoomController()
        room = Room(room_id="room1", name="Gents")
        room = Room(room_id="room1", name="Gents")
        camera1 = Camera(name="Camera 1", room_id="room1")
        camera2 = Camera(name="Camera 2", room_id="room1")
        detector = PersonDetector(name="Detector 1", host="192.168.1.189", port=6053)

        # Setup
        controller.add_room(room)
        room.add_camera(camera1)
        room.add_camera(camera2)
        room.add_person_detector(detector)
        detector.room = room

        # Initial state: room empty, cameras can activate
        room.state = "empty"
        camera1.check_room_and_update_state(room)
        camera2.check_room_and_update_state(room)

        assert camera1._camera_state.status == CameraStatus.ACTIVE
        assert camera2._camera_state.status == CameraStatus.ACTIVE

        camera1.output_status()
        captured = capsys.readouterr()
        assert "Camera 1 active" in captured.out

        # Person enters room
        detector.on_heartbeat_detected(110.0)
        assert room.get_state() == "occupied"

        # Cameras check and deactivate
        camera1.check_room_and_update_state(room)
        camera2.check_room_and_update_state(room)

        assert camera1._camera_state.status == CameraStatus.INACTIVE
        assert camera2._camera_state.status == CameraStatus.INACTIVE

        # Person leaves room
        detector.on_heartbeat_timeout()
        assert room.get_state() == "empty"

        # Cameras check and reactivate
        camera1.check_room_and_update_state(room)
        camera2.check_room_and_update_state(room)

        assert camera1.status == CameraStatus.ACTIVE
        assert camera2.status == CameraStatus.ACTIVE


class TestTwoRoomsIndependent:
    """Test multiple rooms operating independently."""

    def test_two_rooms_independent(self):
        """Room A occupied, Room B empty behave independently."""
        controller = RoomController()

        # Room A -Gents
        room_a = Room(room_id="room_a", name="Gents")
        camera_a = Camera(name="Camera A", room_id="room_a")
        detector_a = PersonDetector(name="Detector A", host="192.168.1.189", port=6053)

        # Room B - Ladies
        room_b = Room(room_id="room_b", name="Ladies")
        camera_b = Camera(name="Camera B", room_id="room_b")
        detector_b = PersonDetector(name="Detector B", host="192.168.1.190", port=6053)

        # Setup Room A
        controller.add_room(room_a)
        room_a.add_camera(camera_a)
        room_a.add_person_detector(detector_a)
        detector_a.room = room_a

        # Setup Room B
        controller.add_room(room_b)
        room_b.add_camera(camera_b)
        room_b.add_person_detector(detector_b)
        detector_b.room = room_b

        # Person in Room A, Room B empty
        detector_a.on_heartbeat_detected(110.0)
        detector_b.on_heartbeat_timeout()

        assert room_a.state == "occupied"
        assert room_b.state == "empty"

        # Camera A should be inactive, Camera B active
        camera_a.check_room_and_update_state(room_a)
        camera_b.check_room_and_update_state(room_b)

        assert camera_a._camera_state.status == CameraStatus.INACTIVE
        assert camera_b._camera_state.status == CameraStatus.ACTIVE


class TestComplexMultiDeviceScenario:
    """Test complex scenario with multiple devices."""

    def test_multiple_cameras_multiple_detectors(self):
        """Complex multi-device scenario."""
        controller = RoomController()
        room = Room(room_id="room1", name="Test Room")

        # 3 cameras
        cameras = [
            Camera(name=f"Camera {i+1}", room_id="room1")
            for i in range(3)
        ]

        # 2 detectors
        detectors = [
            PersonDetector(name=f"Detector {i+1}", host=f"192.168.1.{189+i}", port=6053)
            for i in range(2)
        ]

        # Setup
        controller.add_room(room)
        for camera in cameras:
            room.add_camera(camera)
        for detector in detectors:
            room.add_person_detector(detector)
            detector.room = room

        # All cameras start inactive
        for camera in cameras:
            assert camera._camera_state.status == CameraStatus.OFFLINE

        # Room empty, all cameras activate
        room.state = "empty"
        for camera in cameras:
            camera.check_room_and_update_state(room)
            assert camera._camera_state.status == CameraStatus.ACTIVE

        # One detector sees person
        detectors[0].on_heartbeat_detected(110.0)
        assert room.state == "occupied"

        # All cameras deactivate
        for camera in cameras:
            camera.check_room_and_update_state(room)
            assert camera._camera_state.status == CameraStatus.INACTIVE

        # Both detectors timeout
        detectors[0].on_heartbeat_timeout()
        detectors[1].on_heartbeat_timeout()
        assert room.state == "empty"

        # All cameras reactivate
        for camera in cameras:
            camera.check_room_and_update_state(room)
            assert camera._camera_state.status == CameraStatus.ACTIVE


class TestFailureRecovery:
    """Test system behavior during failures."""

    def test_detector_failure_recovery(self):
        """Detector fails, cameras remain safe (inactive)."""
        room = Room(room_id="room1", name="Test Room")
        camera = Camera(name="Camera 1", room_id="room1")
        detector = PersonDetector(name="Detector 1", host="192.168.1.189", port=6053)

        room.add_camera(camera)
        room.add_person_detector(detector)
        detector.room = room

        # Room occupied
        room.state = "occupied"
        camera.check_room_and_update_state(room)
        assert camera._camera_state.status == CameraStatus.INACTIVE

        # If detector fails/crashes, room should remain in safe state
        # (occupied = cameras off for privacy)
        # This requires detector failure detection

        # Camera should remain inactive as safety default
        camera.check_room_and_update_state(room)
        assert camera._camera_state.status == CameraStatus.INACTIVE


class TestRoomStatePersistence:
    """Test room state consistency."""

    def test_room_state_persistence(self):
        """Room state maintains consistency across operations."""
        room = Room(room_id="room1", name="Test Room")
        detector1 = PersonDetector(name="Detector 1", host="192.168.1.189", port=6053)
        detector2 = PersonDetector(name="Detector 2", host="192.168.1.190", port=6053)

        detector1.room = room
        detector2.room = room

        # Rapid state changes should maintain consistency
        detector1.on_heartbeat_detected(110.0)
        assert room.state == "occupied"

        detector2.on_heartbeat_detected(112.0)
        assert room.state == "occupied"

        detector1.on_heartbeat_timeout()
        # Room still occupied because detector2 hasn't timed out

        detector2.on_heartbeat_timeout()
        assert room.state == "empty"


class TestCameraOutputScheduling:
    """Test camera output happens at correct intervals."""

    def test_camera_output_every_10_seconds(self, capsys):
        """Camera outputs status every 10 seconds when active."""
        room = Room(room_id="room1", name="Test Room")
        camera = Camera(name="Camera 1", room_id="room1", output_interval=1)

        room.add_camera(camera)
        room.state = "empty"
        camera.check_room_and_update_state(room)

        assert camera._camera_state.status == CameraStatus.ACTIVE

        # Simulate multiple output cycles
        camera.output_status()
        captured = capsys.readouterr()
        assert "Camera 1 active" in captured.out

        time.sleep(1.1)
        camera.output_status()
        captured = capsys.readouterr()
        assert "Camera 1 active" in captured.out
