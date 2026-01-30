"""
Test suite for PersonDetector class - Person detection behavior.
"""

import pytest
import asyncio
from unittest.mock import Mock, patch, MagicMock, AsyncMock
from gel_controller.person_detector import PersonDetector
from gel_controller.room import Room


class TestPersonDetectorInitialization:
    """Test PersonDetector initialization and properties."""

    def test_person_detector_initialization(self):
        """Detector starts with correct defaults."""
        detector = PersonDetector(
            name="Detector 1",
            host="192.168.1.189",
            port=6053,
            encryption_key=None
        )

        assert detector.get_name() == "Detector 1"
        assert detector.get_host() == "192.168.1.189"
        assert detector.get_port() == 6053
        assert detector.get_heartbeat_timeout() == 10  # default


class TestPersonDetectorProperties:
    """Test PersonDetector property getters and setters."""

    def test_person_detector_getters_setters(self):
        """All properties have working getters/setters."""
        detector = PersonDetector(name="Detector 1", host="192.168.1.189", port=6053)

        # Name
        assert detector.get_name() == "Detector 1"
        detector.set_name("Detector 2")
        assert detector.get_name() == "Detector 2"

        # Host
        assert detector.get_host() == "192.168.1.189"
        detector.set_host("192.168.1.190")
        assert detector.get_host() == "192.168.1.190"

        # Port
        assert detector.get_port() == 6053
        detector.set_port(6054)
        assert detector.get_port() == 6054

        # Heartbeat timeout
        detector.set_heartbeat_timeout(15)
        assert detector.get_heartbeat_timeout() == 15

    def test_person_detector_room_reference(self):
        """Detector can get/set room reference."""
        detector = PersonDetector(name="Detector 1", host="192.168.1.189", port=6053)
        room = Room(room_id="room1", name="Test Room")

        detector.set_room(room)
        assert detector.get_room() == room


class TestPersonDetectorRoomInteraction:
    """Test PersonDetector updating room state."""

    def test_detector_updates_room_state_occupied(self):
        """Heartbeat detected → room occupied."""
        room = Room(room_id="room1", name="Test Room")
        detector = PersonDetector(name="Detector 1", device_pattern="seeed")
        detector.set_room(room)

        # Simulate heartbeat detection
        detector.on_heartbeat_detected(110.0)

        assert room.get_state() == "occupied"

    def test_detector_updates_room_state_empty(self):
        """No heartbeat → room empty."""
        room = Room(room_id="room1", name="Test Room")
        detector = PersonDetector(name="Detector 1", device_pattern="seeed")
        detector.set_room(room)

        room.set_state("occupied")  # Start occupied

        # Simulate timeout (no heartbeat)
        detector.on_heartbeat_timeout()

        assert room.get_state() == "empty"

    def test_detector_does_not_communicate_with_cameras(self):
        """Detector only updates room state, not cameras directly."""
        room = Room(room_id="room1", name="Test Room")
        detector = PersonDetector(name="Detector 1", device_pattern="seeed")
        from gel_controller.camera import Camera
        camera = Camera(name="Camera 1", room_id="room1")

        room.add_camera(camera)


class TestPersonDetectorRoomInteraction:
    """Test PersonDetector updating room state."""

    def test_detector_updates_room_state_occupied(self):
        """Heartbeat detected → room occupied."""
        room = Room(room_id="room1", name="Test Room")
        detector = PersonDetector(name="Detector 1", host="192.168.1.189", port=6053)
        detector.set_room(room)

        # Simulate heartbeat detection
        detector.on_heartbeat_detected(110.0)

        assert room.get_state() == "occupied"

    def test_detector_updates_room_state_empty(self):
        """No heartbeat → room empty."""
        room = Room(room_id="room1", name="Test Room")
        detector = PersonDetector(name="Detector 1", host="192.168.1.189", port=6053)
        detector.set_room(room)

        room.set_state("occupied")  # Start occupied

        # Simulate timeout (no heartbeat)
        detector.on_heartbeat_timeout()

        assert room.get_state() == "empty"

    def test_detector_does_not_communicate_with_cameras(self):
        """Detector only updates room state, not cameras directly."""
        room = Room(room_id="room1", name="Test Room")
        detector = PersonDetector(name="Detector 1", host="192.168.1.189", port=6053)
        from gel_controller.camera import Camera
        camera = Camera(name="Camera 1", room_id="room1")

        room.add_camera(camera)
        detector.set_room(room)

        # Detector should not have direct reference to cameras
        assert not hasattr(detector, 'cameras')
        assert not hasattr(detector, '_cameras')

        # Detector only knows about room
        assert detector.get_room() == room


class TestPersonDetectorHeartbeatTimeout:
    """Test heartbeat timeout logic."""

    def test_detector_heartbeat_timeout(self):
        """10 second timeout works correctly."""
        room = Room(room_id="room1", name="Test Room")
        detector = PersonDetector(name="Detector 1", host="192.168.1.189", port=6053)
        detector.set_room(room)

        # Set room occupied
        room.set_state("occupied")

        # Simulate 10 seconds passing without heartbeat
        import time
        detector._last_heartbeat_time = time.time() - 11
        detector.check_heartbeat_timeout()

        assert room.get_state() == "empty"


class TestMultipleDetectors:
    """Test multiple detectors in same room."""

    def test_multiple_detectors_same_room(self):
        """Multiple detectors can update same room."""
        room = Room(room_id="room1", name="Test Room")
        detector1 = PersonDetector(name="Detector 1", host="192.168.1.189", port=6053)
        detector2 = PersonDetector(name="Detector 2", host="192.168.1.190", port=6053)

        detector1.set_room(room)
        detector2.set_room(room)

        room.add_person_detector(detector1)
        room.add_person_detector(detector2)

        # If either detector sees heartbeat, room is occupied
        detector1.on_heartbeat_detected(110.0)
        assert room.get_state() == "occupied"

        # Both must timeout before room is empty
        detector1.on_heartbeat_timeout()
        # Room still occupied because detector2 hasn't timed out

        detector2.on_heartbeat_timeout()
        # Now room should be empty
        assert room.get_state() == "empty"


class TestPersonDetectorAPIConnection:
    """Test aioesphomeapi connection functionality."""

    @pytest.mark.asyncio
    async def test_detector_connects_to_device(self, mock_aioesphomeapi):
        """Detector connects to ESPHome device using aioesphomeapi."""
        detector = PersonDetector(
            name="Detector 1",
            host="192.168.1.189",
            port=6053,
            encryption_key=None
        )

        await detector.connect()

        mock_aioesphomeapi.connect.assert_called_once()

    @pytest.mark.asyncio
    async def test_detector_subscribes_to_states(self, mock_aioesphomeapi):
        """Detector subscribes to sensor state changes."""
        detector = PersonDetector(
            name="Detector 1",
            host="192.168.1.189",
            port=6053,
            encryption_key=None
        )

        await detector.connect()
        await detector.subscribe_to_states()

        mock_aioesphomeapi.subscribe_states.assert_called_once()


class TestPersonDetectorHeartbeatHandling:
    """Test handling heartbeat sensor state changes."""

    def test_detector_handles_heartbeat_state(self, mock_aioesphomeapi):
        """Detector processes heartbeat sensor state changes."""
        room = Room(room_id="room1", name="Test Room")
        detector = PersonDetector(name="Detector 1", host="192.168.1.189", port=6053)
        detector.set_room(room)

        # Simulate heartbeat state from ESPHome API
        state = Mock()
        state.key = 1  # Heartbeat sensor key
        state.state = 110.0
        state.name = 'Real-time heart rate'

        detector.handle_state_change(state)

        assert room.get_state() == "occupied"

    def test_detector_handles_zero_heartbeat(self, mock_aioesphomeapi):
        """Zero heartbeat should not trigger occupied."""
        room = Room(room_id="room1", name="Test Room")
        detector = PersonDetector(name="Detector 1", host="192.168.1.189", port=6053)
        detector.set_room(room)

        # Zero heartbeat state
        state = Mock()
        state.key = 1
        state.state = 0.0
        state.name = 'Real-time heart rate'

        detector.handle_state_change(state)

        assert room.get_state() != "occupied"

    def test_detector_ignores_other_sensors(self, mock_aioesphomeapi):
        """Detector ignores non-heartbeat sensor updates."""
        room = Room(room_id="room1", name="Test Room")
        detector = PersonDetector(name="Detector 1", host="192.168.1.189", port=6053)
        detector.set_room(room)

        # Different sensor (not heartbeat)
        state = Mock()
        state.key = 2
        state.state = 50.0
        state.name = 'Distance to detection object'

        detector.handle_state_change(state)

        # Room state should not change
        assert room.get_state() == "empty"


        assert room.get_state() != "occupied"
