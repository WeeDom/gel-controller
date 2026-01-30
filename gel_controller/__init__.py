"""
Gel-controller (Guard-e-loo) - Privacy-respecting room monitoring system.
"""

__version__ = "0.1.0"

from .room import Room
from .camera import Camera
from .person_detector import PersonDetector
from .room_controller import RoomController

__all__ = [
    "Room",
    "Camera",
    "PersonDetector",
    "RoomController",
]
