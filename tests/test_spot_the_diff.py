"""Tests for composite spot-the-diff pairing and person safety gate behavior."""

import json
from pathlib import Path

import spot_the_diff


class _FakeBlock:
    def __init__(self, text: str):
        self.text = text


class _FakeResponse:
    def __init__(self, text: str):
        self.content = [_FakeBlock(text)]


class _FakeMessages:
    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("Unexpected extra Anthropic call")
        return _FakeResponse(self._responses.pop(0))


class _FakeAnthropicClient:
    def __init__(self, api_key: str, responses: list[str], call_store: dict):
        self.api_key = api_key
        self.messages = _FakeMessages(responses)
        call_store["messages"] = self.messages


def _write_jpeg(path: Path) -> None:
    path.write_bytes(b"\xff\xd8\xff\xe0test\xff\xd9")


def test_analyze_changeset_set_uses_latest_per_camera(monkeypatch, tmp_path):
    captures_dir = tmp_path / "captures"
    captures_dir.mkdir()

    baseline_cam1_old = captures_dir / "baseline-101-cam1-20260101_000001_000001.jpeg"
    baseline_cam1_new = captures_dir / "baseline-101-cam1-20260101_000002_000001.jpeg"
    baseline_cam2 = captures_dir / "baseline-101-cam2-20260101_000001_000001.jpeg"

    capture_cam1_old = captures_dir / "capture-101-cam1-20260101_000003_000001.jpeg"
    capture_cam1_new = captures_dir / "capture-101-cam1-20260101_000004_000001.jpeg"
    capture_cam2 = captures_dir / "capture-101-cam2-20260101_000003_000001.jpeg"

    for path in [
        baseline_cam1_old,
        baseline_cam1_new,
        baseline_cam2,
        capture_cam1_old,
        capture_cam1_new,
        capture_cam2,
    ]:
        _write_jpeg(path)

    captured = {}

    def fake_run_analysis(pairs, model):
        captured["pairs"] = pairs
        captured["model"] = model
        return json.dumps({"ok": True, "count": len(pairs)})

    monkeypatch.setattr(spot_the_diff, "run_analysis", fake_run_analysis)

    raw = spot_the_diff.analyze_changeset_set(
        changeset_paths=None,
        room_id="101",
        captures_dir=captures_dir,
        baseline_db=tmp_path / "missing.db",
        model="fake-model",
    )

    parsed = json.loads(raw)
    assert parsed["ok"] is True
    assert parsed["count"] == 2
    assert captured["model"] == "fake-model"

    pair_map = {
        (pair.baseline.room_id, pair.baseline.camera_name): pair
        for pair in captured["pairs"]
    }

    assert set(pair_map.keys()) == {("101", "cam1"), ("101", "cam2")}
    assert pair_map[("101", "cam1")].baseline.path.name == baseline_cam1_new.name
    assert pair_map[("101", "cam1")].capture_path.name == capture_cam1_new.name
    assert pair_map[("101", "cam2")].baseline.path.name == baseline_cam2.name
    assert pair_map[("101", "cam2")].capture_path.name == capture_cam2.name


def test_run_analysis_stops_immediately_on_person(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    baseline1 = tmp_path / "baseline-101-cam1-20260101_000001_000001.jpeg"
    baseline2 = tmp_path / "baseline-101-cam2-20260101_000001_000001.jpeg"
    capture1 = tmp_path / "capture-101-cam1-20260101_000002_000001.jpeg"
    capture2 = tmp_path / "capture-101-cam2-20260101_000002_000001.jpeg"
    for path in [baseline1, baseline2, capture1, capture2]:
        _write_jpeg(path)

    pair1 = spot_the_diff.ImagePair(
        baseline=spot_the_diff.BaselineImage(
            path=baseline1,
            room_id="101",
            camera_name="cam1",
            timestamp="20260101_000001_000001",
        ),
        capture_path=capture1,
        capture_timestamp="20260101_000002_000001",
    )
    pair2 = spot_the_diff.ImagePair(
        baseline=spot_the_diff.BaselineImage(
            path=baseline2,
            room_id="101",
            camera_name="cam2",
            timestamp="20260101_000001_000001",
        ),
        capture_path=capture2,
        capture_timestamp="20260101_000002_000001",
    )

    calls = {}

    responses = [
        json.dumps({"person_detected": True, "confidence": 0.98, "reason": "visible person"}),
    ]

    def fake_anthropic(api_key: str):
        return _FakeAnthropicClient(api_key=api_key, responses=responses, call_store=calls)

    monkeypatch.setattr(spot_the_diff, "Anthropic", fake_anthropic)

    raw = spot_the_diff.run_analysis([pair1, pair2], model="fake-opus")
    parsed = json.loads(raw)

    assert parsed["person_detected"] is True
    assert parsed["overall_verdict"] == "uncertain"
    assert parsed["changesets"] == []
    assert parsed["stop_reason"]["camera_name"] == "cam1"
    assert parsed["stop_reason"]["capture_file"] == capture1.name

    # One safety call only: processing must stop immediately.
    assert len(calls["messages"].calls) == 1


def test_run_analysis_composite_after_safe_gates(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    baseline1 = tmp_path / "baseline-101-cam1-20260101_000001_000001.jpeg"
    baseline2 = tmp_path / "baseline-101-cam2-20260101_000001_000001.jpeg"
    capture1 = tmp_path / "capture-101-cam1-20260101_000002_000001.jpeg"
    capture2 = tmp_path / "capture-101-cam2-20260101_000002_000001.jpeg"
    for path in [baseline1, baseline2, capture1, capture2]:
        _write_jpeg(path)

    pairs = [
        spot_the_diff.ImagePair(
            baseline=spot_the_diff.BaselineImage(
                path=baseline1,
                room_id="101",
                camera_name="cam1",
                timestamp="20260101_000001_000001",
            ),
            capture_path=capture1,
            capture_timestamp="20260101_000002_000001",
        ),
        spot_the_diff.ImagePair(
            baseline=spot_the_diff.BaselineImage(
                path=baseline2,
                room_id="101",
                camera_name="cam2",
                timestamp="20260101_000001_000001",
            ),
            capture_path=capture2,
            capture_timestamp="20260101_000002_000001",
        ),
    ]

    calls = {}
    responses = [
        json.dumps({"person_detected": False, "confidence": 0.80, "reason": "empty room"}),
        json.dumps({"person_detected": False, "confidence": 0.76, "reason": "empty room"}),
        json.dumps(
            {
                "person_detected": False,
                "overall_verdict": "minor_change",
                "full_report": "Minor differences only",
                "changesets": [
                    {
                        "room_id": "101",
                        "camera_name": "cam1",
                        "baseline_file": baseline1.name,
                        "capture_file": capture1.name,
                        "status": "minor_change",
                        "differences": ["small object moved"],
                        "confidence": 0.72,
                    },
                    {
                        "room_id": "101",
                        "camera_name": "cam2",
                        "baseline_file": baseline2.name,
                        "capture_file": capture2.name,
                        "status": "no_change",
                        "differences": [],
                        "confidence": 0.87,
                    },
                ],
                "recommended_actions": ["monitor"],
            }
        ),
    ]

    def fake_anthropic(api_key: str):
        return _FakeAnthropicClient(api_key=api_key, responses=responses, call_store=calls)

    monkeypatch.setattr(spot_the_diff, "Anthropic", fake_anthropic)

    raw = spot_the_diff.run_analysis(pairs, model="fake-opus")
    parsed = json.loads(raw)

    assert parsed["person_detected"] is False
    assert parsed["overall_verdict"] == "minor_change"
    assert len(parsed["changesets"]) == 2

    # Two safety calls + one composite call.
    assert len(calls["messages"].calls) == 3

    final_call = calls["messages"].calls[2]
    assert final_call["model"] == "fake-opus"
    assert final_call["max_tokens"] == 1400
