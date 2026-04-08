#!/usr/bin/env python3
"""
Compare baseline/capture image pairs and send a composite analysis request to Anthropic.

Usage examples:
    python spot_the_diff.py --changeset captures/capture-101-cam1-20260226_162537_630394.jpeg
    python spot_the_diff.py --room-id 101
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from anthropic import Anthropic
from dotenv import load_dotenv

DEFAULT_MODEL = "claude-opus-4-5-20251101"

BASELINE_RE = re.compile(
    r"^baseline-(?P<room_id>[^-]+)-(?P<camera_name>.+)-(?P<timestamp>\d{8}_\d{6}(?:_\d+)?)\.jpe?g$",
    re.IGNORECASE,
)

CAPTURE_RE = re.compile(
    r"^(?P<tag>capture|baseline)-(?P<room_id>[^-]+)-(?P<camera_name>.+)-(?P<timestamp>\d{8}_\d{6}(?:_\d+)?)\.jpe?g$",
    re.IGNORECASE,
)


@dataclass
class BaselineImage:
    path: Path
    room_id: str
    camera_name: str
    timestamp: str
    location: Optional[str] = None


@dataclass
class ImagePair:
    baseline: BaselineImage
    capture_path: Path
    capture_timestamp: str


def parse_capture_name(path: Path) -> Optional[Dict[str, str]]:
    match = CAPTURE_RE.match(path.name)
    return match.groupdict() if match else None


def parse_baseline_name(path: Path) -> Optional[Dict[str, str]]:
    match = BASELINE_RE.match(path.name)
    return match.groupdict() if match else None


def encode_image(path: Path) -> Tuple[str, str]:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        mime = "image/jpeg"
    elif suffix == ".png":
        mime = "image/png"
    else:
        raise ValueError(f"Unsupported image format: {path}")

    data = path.read_bytes()
    return base64.b64encode(data).decode("ascii"), mime


def load_latest_location_by_camera(db_path: Path) -> Dict[str, str]:
    if not db_path.exists():
        return {}

    query = """
        SELECT b.camera_name, b.location
        FROM baselines b
        INNER JOIN (
            SELECT camera_name, MAX(captured_at) AS max_captured_at
            FROM baselines
            GROUP BY camera_name
        ) latest
        ON latest.camera_name = b.camera_name
        AND latest.max_captured_at = b.captured_at
    """

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(query).fetchall()

    return {camera_name: location for camera_name, location in rows}


def select_latest_baselines(captures_dir: Path, room_id: Optional[str], db_path: Path) -> List[BaselineImage]:
    latest_by_camera: Dict[Tuple[str, str], BaselineImage] = {}
    location_by_camera = load_latest_location_by_camera(db_path)

    for path in captures_dir.glob("baseline-*.jp*"):
        parts = parse_baseline_name(path)
        if not parts:
            continue

        baseline_room_id = parts["room_id"]
        if room_id and baseline_room_id != room_id:
            continue

        key = (baseline_room_id, parts["camera_name"])
        candidate = BaselineImage(
            path=path,
            room_id=baseline_room_id,
            camera_name=parts["camera_name"],
            timestamp=parts["timestamp"],
            location=location_by_camera.get(parts["camera_name"]),
        )

        current = latest_by_camera.get(key)
        if current is None or candidate.timestamp > current.timestamp:
            latest_by_camera[key] = candidate

    return sorted(latest_by_camera.values(), key=lambda b: (b.room_id, b.camera_name))


def pick_latest_changeset(captures_dir: Path, room_id: Optional[str]) -> Optional[Path]:
    candidates: List[Tuple[str, Path]] = []
    for path in captures_dir.glob("capture-*.jp*"):
        parts = parse_capture_name(path)
        if not parts:
            continue
        if room_id and parts["room_id"] != room_id:
            continue
        candidates.append((parts["timestamp"], path))

    if not candidates:
        return None

    return max(candidates, key=lambda x: x[0])[1]


def select_latest_captures(captures_dir: Path, room_id: Optional[str]) -> Dict[Tuple[str, str], Tuple[Path, str]]:
    latest_by_camera: Dict[Tuple[str, str], Tuple[Path, str]] = {}
    for path in captures_dir.glob("capture-*.jp*"):
        parts = parse_capture_name(path)
        if not parts:
            continue

        capture_room_id = parts["room_id"]
        if room_id and capture_room_id != room_id:
            continue

        key = (capture_room_id, parts["camera_name"])
        candidate = (path, parts["timestamp"])
        current = latest_by_camera.get(key)
        if current is None or candidate[1] > current[1]:
            latest_by_camera[key] = candidate

    return latest_by_camera


def build_image_pairs(
    baselines: List[BaselineImage],
    changesets: Sequence[Path],
) -> List[ImagePair]:
    baseline_lookup: Dict[Tuple[str, str], BaselineImage] = {
        (b.room_id, b.camera_name): b for b in baselines
    }
    pairs: List[ImagePair] = []

    for capture_path in changesets:
        meta = parse_capture_name(capture_path)
        if not meta or meta["tag"].lower() != "capture":
            continue

        baseline = baseline_lookup.get((meta["room_id"], meta["camera_name"]))
        if not baseline:
            continue

        pairs.append(
            ImagePair(
                baseline=baseline,
                capture_path=capture_path,
                capture_timestamp=meta["timestamp"],
            )
        )

    return sorted(
        pairs,
        key=lambda p: (p.baseline.room_id, p.baseline.camera_name, p.capture_timestamp),
    )


def build_person_gate_prompt(pair: ImagePair) -> str:
    return (
        "Safety gate: determine if a visible person is present in the NEW capture image.\n"
        "If you are uncertain, treat as person present.\n\n"
        "Context:\n"
        f"- room_id={pair.baseline.room_id}\n"
        f"- camera={pair.baseline.camera_name}\n"
        f"- capture_file={pair.capture_path.name}\n\n"
        "Return ONLY JSON:\n"
        "{\n"
        '  "person_detected": true,\n'
        '  "confidence": 0.0,\n'
        '  "reason": "short explanation"\n'
        "}\n"
        "Do not include markdown fences."
    )


def build_composite_prompt(pairs: List[ImagePair]) -> str:
    pair_lines = []
    for idx, pair in enumerate(pairs, start=1):
        baseline = pair.baseline
        location = baseline.location or "unknown"
        pair_lines.append(
            f"{idx}. room_id={baseline.room_id}, camera={baseline.camera_name}, "
            f"location={location}, baseline={baseline.path.name}, "
            f"capture={pair.capture_path.name}"
        )

    return (
        "You are comparing MULTIPLE baseline/capture image pairs in one composite analysis.\n"
        "Each pair is baseline -> new capture for the same room_id and camera.\n"
        "Analyze each pair independently, then provide a full combined report.\n\n"
        "Image pairs:\n"
        f"{chr(10).join(pair_lines)}\n\n"
        "Return ONLY JSON with this schema:\n"
        "{\n"
        '  "person_detected": false,\n'
        '  "overall_verdict": "no_change|minor_change|significant_change|uncertain",\n'
        '  "full_report": "short combined summary",\n'
        '  "changesets": [\n'
        "    {\n"
        '      "room_id": "string",\n'
        '      "camera_name": "string",\n'
        '      "baseline_file": "string",\n'
        '      "capture_file": "string",\n'
        '      "status": "no_change|minor_change|significant_change|uncertain",\n'
        '      "differences": ["list visible differences"],\n'
        '      "confidence": 0.0\n'
        "    }\n"
        "  ],\n"
        '  "recommended_actions": ["list"]\n'
        "}\n"
        "Do not include markdown fences."
    )


def _extract_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    return {}


def _message_text(response: Any) -> str:
    chunks: List[str] = []
    for block in response.content or []:
        text = getattr(block, "text", None)
        if text:
            chunks.append(text)
    return "\n".join(chunks).strip()


def run_analysis(
    pairs: List[ImagePair],
    model: str,
) -> str:
    load_dotenv()
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    client = Anthropic(api_key=api_key)

    safety_model = os.getenv("SPOT_THE_DIFF_SAFETY_MODEL", "claude-3-5-haiku-latest")

    for pair in pairs:
        capture_b64, capture_mime = encode_image(pair.capture_path)
        safety_content: List[Dict[str, Any]] = [
            {"type": "text", "text": build_person_gate_prompt(pair)},
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": capture_mime,
                    "data": capture_b64,
                },
            },
        ]
        safety_response = client.messages.create(
            model=safety_model,
            max_tokens=220,
            messages=[{"role": "user", "content": safety_content}],  # type: ignore[arg-type]
        )
        safety_text = _message_text(safety_response)
        if not safety_text:
            raise RuntimeError("Empty safety-gate response from Anthropic")

        safety_parsed = _extract_json(safety_text)
        if bool(safety_parsed.get("person_detected", False)):
            blocked = {
                "person_detected": True,
                "overall_verdict": "uncertain",
                "full_report": (
                    "Processing stopped immediately because a person was detected in a capture image. "
                    "No further image pairs were analyzed."
                ),
                "changesets": [],
                "recommended_actions": [
                    "Discard or quarantine this capture set for privacy",
                    "Retry capture when occupancy sensors confirm the room is empty",
                ],
                "stop_reason": {
                    "room_id": pair.baseline.room_id,
                    "camera_name": pair.baseline.camera_name,
                    "capture_file": pair.capture_path.name,
                    "reason": safety_parsed.get("reason", "person detected by safety gate"),
                    "confidence": safety_parsed.get("confidence"),
                },
            }
            return json.dumps(blocked)

    content: List[Dict[str, Any]] = []
    content.append({"type": "text", "text": build_composite_prompt(pairs)})

    for pair in pairs:
        baseline = pair.baseline
        baseline_b64, baseline_mime = encode_image(baseline.path)
        capture_b64, capture_mime = encode_image(pair.capture_path)

        content.append({
            "type": "text",
            "text": (
                "PAIR | "
                f"room_id={baseline.room_id} | camera={baseline.camera_name} | "
                f"baseline={baseline.path.name} | capture={pair.capture_path.name}"
            ),
        })
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": baseline_mime,
                    "data": baseline_b64,
                },
            }
        )
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": capture_mime,
                    "data": capture_b64,
                },
            }
        )

    response = client.messages.create(
        model=model,
        max_tokens=1400,
        messages=[{"role": "user", "content": content}],  # type: ignore[arg-type]
    )

    if not response.content:
        raise RuntimeError("Empty response from Anthropic")

    return _message_text(response)


def analyze_changeset_file(
    changeset_path: Path,
    room_id: Optional[str] = None,
    captures_dir: Path = Path("captures"),
    baseline_db: Path = Path("logs/baselines.db"),
    model: str = DEFAULT_MODEL,
) -> str:
    """Analyze a specific changeset against latest room baselines via Anthropic."""
    return analyze_changeset_set(
        changeset_paths=[changeset_path],
        room_id=room_id,
        captures_dir=captures_dir,
        baseline_db=baseline_db,
        model=model,
    )


def analyze_changeset_set(
    changeset_paths: Optional[Sequence[Path]] = None,
    room_id: Optional[str] = None,
    captures_dir: Path = Path("captures"),
    baseline_db: Path = Path("logs/baselines.db"),
    model: str = DEFAULT_MODEL,
) -> str:
    """Analyze one or more changesets as baseline/capture pairs in a single composite report."""
    if not captures_dir.exists():
        raise FileNotFoundError(f"captures directory not found: {captures_dir}")

    baselines = select_latest_baselines(captures_dir, room_id, baseline_db)
    if not baselines:
        raise RuntimeError("No baseline images found for the requested scope")

    if changeset_paths is None:
        latest_by_camera = select_latest_captures(captures_dir, room_id)
        candidate_changesets = [path for path, _ in latest_by_camera.values()]
    else:
        candidate_changesets = list(changeset_paths)

    if not candidate_changesets:
        raise RuntimeError("No changeset images found for the requested scope")

    missing = [path for path in candidate_changesets if not path.exists()]
    if missing:
        raise FileNotFoundError(f"changeset image not found: {missing[0]}")

    pairs = build_image_pairs(baselines, candidate_changesets)
    if not pairs:
        raise RuntimeError("No baseline/capture pairs found (camera names or room IDs may not match)")

    return run_analysis(pairs, model)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare latest baselines with one or more changesets via Anthropic")
    parser.add_argument("--captures-dir", default="captures", help="Directory containing capture images")
    parser.add_argument("--changeset", help="Path to changeset image; defaults to latest capture-*.jpeg")
    parser.add_argument("--room-id", help="Restrict to room ID")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Anthropic model")
    parser.add_argument("--baseline-db", default="logs/baselines.db", help="Path to baseline sqlite db")
    parser.add_argument("--output", help="Optional path to write raw Anthropic response")
    args = parser.parse_args()

    captures_dir = Path(args.captures_dir)
    if not captures_dir.exists():
        print(f"captures directory not found: {captures_dir}", file=sys.stderr)
        return 1

    requested_changesets: Optional[List[Path]] = None
    if args.changeset:
        single = Path(args.changeset)
        if not single.exists():
            print("Specified --changeset file does not exist.", file=sys.stderr)
            return 3
        requested_changesets = [single]
    else:
        latest = select_latest_captures(captures_dir, args.room_id)
        if not latest:
            print("No changeset image found. Provide --changeset or create capture-*.jpeg files first.", file=sys.stderr)
            return 3
        requested_changesets = [path for path, _ in latest.values()]

    try:
        raw = analyze_changeset_set(
            changeset_paths=requested_changesets,
            room_id=args.room_id,
            captures_dir=captures_dir,
            baseline_db=Path(args.baseline_db),
            model=args.model,
        )
    except Exception as exc:
        print(f"Anthropic request failed: {exc}", file=sys.stderr)
        return 4

    if args.output:
        Path(args.output).write_text(raw + "\n", encoding="utf-8")

    print(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
