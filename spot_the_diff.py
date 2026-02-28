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


def build_prompt(baselines: List[BaselineImage], changeset_path: Path) -> str:
    baseline_lines = []
    for idx, baseline in enumerate(baselines, start=1):
        location = baseline.location or "unknown"
        baseline_lines.append(
            f"{idx}. room_id={baseline.room_id}, camera={baseline.camera_name}, "
            f"location={location}, timestamp={baseline.timestamp}, file={baseline.path.name}"
        )

    return (
        "You are comparing one NEW changeset image against multiple baseline reference images.\n"
        "Determine whether there are meaningful visual changes from baseline.\n\n"
        "Baseline metadata:\n"
        f"{chr(10).join(baseline_lines)}\n\n"
        f"Changeset file: {changeset_path.name}\n\n"
        "Return ONLY JSON with this schema:\n"
        "{\n"
        '  "overall_verdict": "no_change|minor_change|significant_change|uncertain",\n'
        '  "summary": "short explanation",\n'
        '  "per_camera": [\n'
        "    {\n"
        '      "camera_name": "string",\n'
        '      "room_id": "string",\n'
        '      "status": "no_change|minor_change|significant_change|uncertain",\n'
        '      "differences": ["list visible differences"],\n'
        '      "confidence": 0.0\n'
        "    }\n"
        "  ],\n"
        '  "recommended_actions": ["list"]\n'
        "}\n"
        "Do not include markdown fences."
    )


def run_analysis(
    baselines: List[BaselineImage],
    changeset_path: Path,
    model: str,
) -> str:
    load_dotenv()
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    client = Anthropic(api_key=api_key)

    content: List[Dict[str, object]] = []
    content.append({"type": "text", "text": build_prompt(baselines, changeset_path)})

    for baseline in baselines:
        img_b64, mime = encode_image(baseline.path)
        content.append({
            "type": "text",
            "text": f"BASELINE | room_id={baseline.room_id} | camera={baseline.camera_name} | timestamp={baseline.timestamp}",
        })
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime,
                    "data": img_b64,
                },
            }
        )

    changeset_b64, changeset_mime = encode_image(changeset_path)
    parts = parse_capture_name(changeset_path)
    changeset_meta = (
        f"room_id={parts['room_id']} camera={parts['camera_name']} timestamp={parts['timestamp']}"
        if parts
        else f"file={changeset_path.name}"
    )
    content.append({"type": "text", "text": f"CHANGESET | {changeset_meta}"})
    content.append(
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": changeset_mime,
                "data": changeset_b64,
            },
        }
    )

    response = client.messages.create(
        model=model,
        max_tokens=1400,
        messages=[{"role": "user", "content": content}],
    )

    if not response.content:
        raise RuntimeError("Empty response from Anthropic")

    return response.content[0].text.strip()


def analyze_changeset_file(
    changeset_path: Path,
    room_id: Optional[str] = None,
    captures_dir: Path = Path("captures"),
    baseline_db: Path = Path("logs/baselines.db"),
    model: str = DEFAULT_MODEL,
) -> str:
    """Analyze a specific changeset against latest room baselines via Anthropic."""
    if not captures_dir.exists():
        raise FileNotFoundError(f"captures directory not found: {captures_dir}")

    if not changeset_path.exists():
        raise FileNotFoundError(f"changeset image not found: {changeset_path}")

    baselines = select_latest_baselines(captures_dir, room_id, baseline_db)
    if not baselines:
        raise RuntimeError("No baseline images found for the requested scope")

    return run_analysis(baselines, changeset_path, model)


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
