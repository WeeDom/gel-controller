import json
import logging

from gel_controller.logging_utils import log_debug_event, log_incident, setup_logging


def test_setup_logging_writes_debug_and_incident_jsonl(tmp_path):
    files = setup_logging(tmp_path)
    logger = logging.getLogger("test.logging")

    log_debug_event(logger, "Heartbeat sensor update", event_type="heartbeat", room_id="101")
    log_incident(logger, "Room became occupied", event_type="room_occupied", room_id="101")

    for handler in logging.getLogger().handlers:
        handler.flush()

    debug_lines = files["debug_log"].read_text(encoding="utf-8").splitlines()
    incident_lines = files["incident_log"].read_text(encoding="utf-8").splitlines()

    assert len(debug_lines) == 2
    assert len(incident_lines) == 1

    debug_entries = [json.loads(line) for line in debug_lines]
    incident_entry = json.loads(incident_lines[0])

    assert debug_entries[0]["event_type"] == "heartbeat"
    assert debug_entries[0]["incident"] is False
    assert debug_entries[1]["event_type"] == "room_occupied"
    assert debug_entries[1]["incident"] is True
    assert incident_entry["message"] == "Room became occupied"
    assert incident_entry["room_id"] == "101"
