from fastapi.testclient import TestClient

from gel_controller.control_api import ControlAPIServer


class _FakeController:
    def __init__(self):
        self.running = True
        self.last_room_id = None

    def is_running(self):
        return self.running

    def get_status(self, include_logs=True, log_lines=80):
        return {"ok": True, "running": self.running, "include_logs": include_logs, "log_lines": log_lines}

    def capture_baseline(self, room_id=None):
        self.last_room_id = room_id
        if room_id == "missing":
            return {"ok": False, "message": "No matching rooms"}
        return {"ok": True, "room_id": room_id}

    def analyze_latest(self, room_id=None):
        self.last_room_id = room_id
        if room_id == "missing":
            return {"ok": False, "message": "No matching rooms"}
        return {"ok": True, "room_id": room_id}


def test_versioned_and_legacy_health_routes():
    server = ControlAPIServer(controller=_FakeController(), host="127.0.0.1", port=8765)
    client = TestClient(server.app())

    legacy = client.get("/health")
    versioned = client.get("/api/v1/health")

    assert legacy.status_code == 200
    assert versioned.status_code == 200
    assert legacy.json()["ok"] is True
    assert versioned.json()["ok"] is True


def test_capture_baseline_versioned_success():
    fake_controller = _FakeController()
    server = ControlAPIServer(controller=fake_controller, host="127.0.0.1", port=8765)
    client = TestClient(server.app())

    response = client.post("/api/v1/capture-baseline", json={"room_id": "101"})

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert fake_controller.last_room_id == "101"


def test_analyze_latest_not_found():
    fake_controller = _FakeController()
    server = ControlAPIServer(controller=fake_controller, host="127.0.0.1", port=8765)
    client = TestClient(server.app())

    response = client.post("/api/v1/analyze-latest", json={"room_id": "missing"})

    assert response.status_code == 404
    assert response.json()["ok"] is False
