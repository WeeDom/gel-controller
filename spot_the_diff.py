#!/usr/bin/env python3
"""
Compare a changeset image against the latest baseline images and send to Anthropic.

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
from typing import Dict, List, Optional, Tuple

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


def build_prompt(baselines: List[BaselineImage], changeset_paths: List[Path]) -> str:
    """Build a multi-camera prompt for one occupancy event (all cameras in a single call)."""
    baseline_lines = []
    for idx, baseline in enumerate(baselines, start=1):
        location = baseline.location or "unknown"
        baseline_lines.append(
            f"{idx}. room_id={baseline.room_id}, camera={baseline.camera_name}, "
            f"location={location}, timestamp={baseline.timestamp}"
        )

    changeset_lines = []
    for idx, path in enumerate(changeset_paths, start=1):
        parts = parse_capture_name(path)
        if parts:
            changeset_lines.append(
                f"{idx}. room_id={parts['room_id']}, camera={parts['camera_name']}, timestamp={parts['timestamp']}"
            )
        else:
            changeset_lines.append(f"{idx}. file={path.name}")

    baseline_meta = "\n".join(baseline_lines) or "(none)"
    changeset_meta = "\n".join(changeset_lines) or "(none)"

    return (
        "You are analysing a MULTI-CAMERA monitoring system.\n"
        "You will receive images from FIXED cameras at different angles of the same room.\n\n"
        "TASK: Compare NEW changeset images against BASELINE reference images to detect changes.\n\n"
        "CRITICAL INSTRUCTIONS:\n"
        "1. Analyse EACH camera view independently first\n"
        "2. For each camera, perform systematic object-by-object comparison\n"
        "3. Objects visible in multiple camera views provide cross-validation — use them\n"
        "4. A change detected in ONE camera is still significant even if other cameras show no change\n"
        "5. Consider camera-specific blind spots and occlusions\n\n"
        "ANALYSIS METHODOLOGY — for each camera pair (baseline vs changeset):\n"
        "  Step 1: Identify the camera's viewing angle and coverage area\n"
        "  Step 2: Enumerate all visible objects and their positions\n"
        "  Step 3: Compare object positions between baseline and changeset\n"
        "  Step 4: Note any objects added, removed, or moved\n"
        "  Step 5: Flag differences with specific location descriptions\n"
        "After individual camera analysis:\n"
        "  Step 6: Cross-reference findings across cameras\n"
        "  Step 7: Resolve ambiguities using multiple viewpoints\n"
        "  Step 8: Determine overall room status\n\n"
        "WHAT TO LOOK FOR:\n"
        "- Object position changes (even small movements matter)\n"
        "- Objects added or removed from scene\n"
        "- Furniture rearrangement\n"
        "- Doors/drawers opened or closed\n"
        "- People or pets that have not yet left\n"
        "- Items on surfaces (desks, tables, shelves, floors)\n"
        "- Clothing, bags, or personal items left behind or moved\n"
        "- Changes in what's visible through doorways/windows\n\n"
        f"BASELINE IMAGES ({len(baselines)} camera(s)):\n"
        f"{baseline_meta}\n\n"
        f"CHANGESET IMAGES ({len(changeset_paths)} camera(s)):\n"
        f"{changeset_meta}\n\n"
        "Return ONLY JSON with this schema:\n"
        "{\n"
        '  "overall_verdict": "no_change|minor_change|significant_change|uncertain",\n'
        '  "summary": "brief description of what changed across all cameras",\n'
        '  "confidence": 0.0,\n'
        '  "per_camera": [\n'
        "    {\n"
        '      "camera_name": "string",\n'
        '      "room_id": "string",\n'
        '      "viewing_angle": "brief description of what this camera sees",\n'
        '      "status": "no_change|minor_change|significant_change|uncertain",\n'
        '      "differences": [\n'
        '        {\n'
        '          "object": "description",\n'
        '          "change_type": "moved|added|removed|modified",\n'
        '          "baseline_position": "description",\n'
        '          "changeset_position": "description or null",\n'
        '          "confidence": 0.0\n'
        '        }\n'
        '      ],\n'
        '      "camera_confidence": 0.0\n'
        "    }\n"
        "  ],\n"
        '  "cross_camera_validation": {\n'
        '    "consistent_findings": ["changes visible in multiple cameras"],\n'
        '    "single_camera_findings": ["changes only visible in one camera"],\n'
        '    "confidence_notes": "explanation"\n'
        '  },\n'
        '  "recommended_actions": ["list"]\n'
        "}\n\n"
        "VERDICT RULES:\n"
        "- If ANY camera shows a moved/added/removed object, overall_verdict must reflect that\n"
        "- 'no_change' requires HIGH confidence across ALL cameras\n"
        "- When in doubt between minor_change and significant_change, choose significant_change\n"
        "- Use spatial descriptors relative to the room, not camera position\n"
        "Do not include markdown fences in your response."
    )


def run_analysis(
    baselines: List[BaselineImage],
    changeset_paths: List[Path],
    model: str,
) -> str:
    """Send all cameras for one event in a single API call, grouped baseline→changeset per camera."""
    load_dotenv()
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    client = Anthropic(api_key=api_key)

    content: List[Dict[str, object]] = []
    content.append({"type": "text", "text": build_prompt(baselines, changeset_paths)})

    # Group changesets by camera name for interleaved baseline→changeset presentation
    baseline_by_cam = {b.camera_name: b for b in baselines}
    changeset_by_cam: Dict[str, Path] = {}
    for path in changeset_paths:
        parts = parse_capture_name(path)
        cam = parts["camera_name"] if parts else path.stem
        changeset_by_cam[cam] = path

    all_cameras = sorted(set(list(baseline_by_cam) + list(changeset_by_cam)))

    for cam in all_cameras:
        if cam in baseline_by_cam:
            b = baseline_by_cam[cam]
            img_b64, mime = encode_image(b.path)
            content.append({
                "type": "text",
                "text": f"BASELINE | camera={b.camera_name} | room_id={b.room_id} | timestamp={b.timestamp}",
            })
            content.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": img_b64}})
        if cam in changeset_by_cam:
            path = changeset_by_cam[cam]
            parts = parse_capture_name(path)
            meta = (
                f"camera={parts['camera_name']} | room_id={parts['room_id']} | timestamp={parts['timestamp']}"
                if parts
                else f"file={path.name}"
            )
            img_b64, mime = encode_image(path)
            content.append({"type": "text", "text": f"CHANGESET | {meta}"})
            content.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": img_b64}})

    response = client.messages.create(
        model=model,
        max_tokens=2048,
        messages=[{"role": "user", "content": content}],
    )

    if not response.content:
        raise RuntimeError("Empty response from Anthropic")

    return response.content[0].text.strip()


def analyze_event_files(
    changeset_paths: List[Path],
    room_id: Optional[str] = None,
    captures_dir: Path = Path("captures"),
    baseline_db: Path = Path("logs/baselines.db"),
    model: str = DEFAULT_MODEL,
) -> str:
    """Analyse all captures from one occupancy event against latest baselines in a single API call."""
    if not captures_dir.exists():
        raise FileNotFoundError(f"captures directory not found: {captures_dir}")

    for path in changeset_paths:
        if not path.exists():
            raise FileNotFoundError(f"changeset image not found: {path}")

    baselines = select_latest_baselines(captures_dir, room_id, baseline_db)
    if not baselines:
        raise RuntimeError("No baseline images found for the requested scope")

    return run_analysis(baselines, changeset_paths, model)


def analyze_changeset_file(
    changeset_path: Path,
    room_id: Optional[str] = None,
    captures_dir: Path = Path("captures"),
    baseline_db: Path = Path("logs/baselines.db"),
    model: str = DEFAULT_MODEL,
) -> str:
    """Analyse a single changeset against latest room baselines (wraps analyze_event_files)."""
    return analyze_event_files(
        changeset_paths=[changeset_path],
        room_id=room_id,
        captures_dir=captures_dir,
        baseline_db=baseline_db,
        model=model,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare latest baselines with a changeset via Anthropic")
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

    changeset_path = Path(args.changeset) if args.changeset else pick_latest_changeset(captures_dir, args.room_id)
    if not changeset_path or not changeset_path.exists():
        print("No changeset image found. Provide --changeset or create a capture-*.jpeg first.", file=sys.stderr)
        return 3

    try:
        raw = analyze_changeset_file(
            changeset_path=changeset_path,
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
