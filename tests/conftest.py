"""
Pytest configuration and fixtures for gel-controller tests.
"""

import pytest
from unittest.mock import Mock, MagicMock, patch, AsyncMock
from datetime import datetime
import asyncio


@pytest.fixture
def mock_aioesphomeapi():
    """Mock aioesphomeapi.APIClient for ESPHome device communication."""
    with patch('aioesphomeapi.APIClient') as mock_client_class:
        mock_client = MagicMock()

        # Create proper mock entities with actual string attributes
        class MockEntity:
            def __init__(self, object_id, key, name, unique_id):
                self.object_id = object_id
                self.key = key
                self.name = name
                self.unique_id = unique_id

        # Mock async methods
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()
        mock_client.device_info = AsyncMock(return_value={
            'name': 'seeedstudio-mr60bha2-kit-8e65b4',
            'mac_address': '8E:65:B4:00:00:01',
            'esphome_version': '2026.1.3',
            'model': 'seeed_xiao_esp32c6'
        })
        mock_client.list_entities_services = AsyncMock(return_value=(
            [
                # Mock sensor entities with proper string attributes
                MockEntity(
                    object_id='real_time_heart_rate',
                    key=1,
                    name='Real-time heart rate',
                    unique_id='sensor-real_time_heart_rate'
                ),
                MockEntity(
                    object_id='distance_to_detection_object',
                    key=2,
                    name='Distance to detection object',
                    unique_id='sensor-distance_to_detection_object'
                ),
            ],
            []  # services
        ))

        # Mock subscribe_states - will be configured in tests
        mock_client.subscribe_states = Mock()

        # Return the mock client instance when APIClient is instantiated
        mock_client_class.return_value = mock_client

        yield mock_client


@pytest.fixture
def mock_zeroconf():
    """Mock zeroconf for device discovery."""
    with patch('zeroconf.Zeroconf') as mock_zc_class, \
         patch('zeroconf.ServiceBrowser') as mock_browser_class:

        mock_zc = MagicMock()
        mock_browser = MagicMock()

        mock_zc_class.return_value = mock_zc
        mock_browser_class.return_value = mock_browser

        yield {'zeroconf': mock_zc, 'browser': mock_browser}

@pytest.fixture
def test_room():
    """Create a test Room instance."""
    from gel_controller.room import Room
    return Room(room_id="test_room_1", name="Test Room")


@pytest.fixture
def test_camera():
    """Create a test Camera instance."""
    from gel_controller.camera import Camera
    return Camera(name="Test Camera 1", room_id="test_room_1")


@pytest.fixture
def test_person_detector(mock_aioesphomeapi):
    """Create a test PersonDetector instance with mocked ESPHome API."""
    from gel_controller.person_detector import PersonDetector
    return PersonDetector(
        name="Test Detector 1",
        host="192.168.1.189",
        port=6053,
        encryption_key=None  # No encryption for tests
    )


@pytest.fixture
def test_room_controller():
    """Create a test RoomController instance."""
    from gel_controller.room_controller import RoomController
    return RoomController()


@pytest.fixture
def populated_room(test_room, test_camera, test_person_detector):
    """Create a room with a camera and person detector."""
    test_room.add_camera(test_camera)
    test_room.add_person_detector(test_person_detector)
    return test_room


@pytest.fixture
def event_loop():
    """Create an event loop for async tests."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()


def simulate_heartbeat_state(mock_client, heart_rate: float):
    """Helper function to simulate a heartbeat sensor state change.

    Args:
        mock_client: The mocked APIClient
        heart_rate: The heart rate value to simulate (e.g., 110.0)
    """
    state = Mock()
    state.key = 1  # Matches the sensor key from mock_aioesphomeapi
    state.state = heart_rate
    state.name = 'Real-time heart rate'

    # Get the callback that was registered with subscribe_states
    if mock_client.subscribe_states.called:
        callback = mock_client.subscribe_states.call_args[0][0]
        callback(state)

    return state


def simulate_no_heartbeat(mock_client):
    """Helper function to simulate heartbeat timeout (no state updates)."""
    # This simulates time passing without heartbeat updates
    # The PersonDetector should handle this with its timeout logic
    pass
