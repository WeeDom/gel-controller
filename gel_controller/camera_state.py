"""
Camera State Management - Handles camera states and transitions.
"""

import logging
from enum import Enum
from datetime import datetime
from typing import Optional, List

logger = logging.getLogger(__name__)


class CameraStatus(Enum):
    """Camera operational states."""
    INACTIVE = "inactive"      # Camera off, not monitoring
    ACTIVE = "active"          # Camera on, monitoring allowed
    RECORDING = "recording"    # Camera actively recording
    ERROR = "error"            # Camera in error state
    CALIBRATING = "calibrating"  # Camera calibrating/initializing
    OFFLINE = "offline"        # Camera disconnected/unreachable


class CameraState:
    """
    Manages camera state with history and transition rules.

    Tracks:
    - Current status
    - State history with timestamps
    - Transition validation
    - Time in current state
    """

    # Valid state transitions
    TRANSITIONS = {
        CameraStatus.OFFLINE: [CameraStatus.CALIBRATING, CameraStatus.INACTIVE, CameraStatus.ACTIVE],
        CameraStatus.CALIBRATING: [CameraStatus.INACTIVE, CameraStatus.ERROR],
        CameraStatus.INACTIVE: [CameraStatus.ACTIVE, CameraStatus.OFFLINE, CameraStatus.ERROR],
        CameraStatus.ACTIVE: [CameraStatus.RECORDING, CameraStatus.INACTIVE, CameraStatus.OFFLINE, CameraStatus.ERROR],
        CameraStatus.RECORDING: [CameraStatus.ACTIVE, CameraStatus.INACTIVE, CameraStatus.OFFLINE, CameraStatus.ERROR],
        CameraStatus.ERROR: [CameraStatus.CALIBRATING, CameraStatus.OFFLINE, CameraStatus.INACTIVE],
    }

    def __init__(self, initial_status: CameraStatus = CameraStatus.OFFLINE):
        """
        Initialize camera state.

        Args:
            initial_status: Starting status (default: OFFLINE)
        """
        self._status = initial_status
        self._entered_at = datetime.now()
        self._history: List[tuple] = [(initial_status, self._entered_at)]
        self._error_message: Optional[str] = None

    @property
    def status(self) -> CameraStatus:
        """Get current status."""
        return self._status

    @property
    def status_value(self) -> str:
        """Get current status string value."""
        return self._status.value

    @property
    def entered_at(self) -> datetime:
        """Get time when current state was entered."""
        return self._entered_at

    @property
    def time_in_state(self) -> float:
        """Get seconds in current state."""
        return (datetime.now() - self._entered_at).total_seconds()

    @property
    def error_message(self) -> Optional[str]:
        """Get error message if in ERROR state."""
        return self._error_message

    @property
    def is_operational(self) -> bool:
        """Check if camera is in operational state (not error/offline)."""
        return self._status in [CameraStatus.INACTIVE, CameraStatus.ACTIVE, CameraStatus.RECORDING]

    @property
    def can_record(self) -> bool:
        """Check if camera can start recording."""
        return self._status == CameraStatus.ACTIVE

    def transition_to(self, new_status: CameraStatus, reason: Optional[str] = None) -> bool:
        """
        Attempt to transition to new status.

        Args:
            new_status: Target status
            reason: Optional reason for transition (e.g., error message)

        Returns:
            True if transition successful, False if invalid
        """
        # Allow same-state "transitions" (no-op)
        if new_status == self._status:
            return True

        # Check if transition is allowed
        if new_status not in self.TRANSITIONS.get(self._status, []):
            logger.warning(
                f"Invalid transition: {self._status.value} → {new_status.value}"
            )
            return False

        # Perform transition
        old_status = self._status
        old_time = self._entered_at
        duration = self.time_in_state

        self._status = new_status
        self._entered_at = datetime.now()
        self._history.append((new_status, self._entered_at))

        # Store error message if transitioning to ERROR
        if new_status == CameraStatus.ERROR:
            self._error_message = reason
        else:
            self._error_message = None

        logger.info(
            f"State transition: {old_status.value} → {new_status.value} "
            f"(was in {old_status.value} for {duration:.1f}s)"
            + (f" - {reason}" if reason else "")
        )

        return True

    def get_history(self, limit: Optional[int] = None) -> List[tuple]:
        """
        Get state history.

        Args:
            limit: Maximum number of entries (None = all)

        Returns:
            List of (status, timestamp) tuples
        """
        if limit:
            return self._history[-limit:]
        return self._history.copy()

    def to_dict(self) -> dict:
        """Export state as dictionary."""
        return {
            "status": self._status.value,
            "entered_at": self._entered_at.isoformat(),
            "time_in_state": self.time_in_state,
            "error_message": self._error_message,
            "is_operational": self.is_operational,
        }

    def __str__(self) -> str:
        """String representation."""
        return f"CameraState({self._status.value}, {self.time_in_state:.1f}s)"

    def __repr__(self) -> str:
        """Debug representation."""
        return f"CameraState(status={self._status}, entered={self._entered_at})"
