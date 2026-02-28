#!/usr/bin/env python3
import argparse
import ipaddress
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse
from pathlib import Path


def is_ip_address(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def normalize_host_like(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None

    if "://" in text:
        parsed = urlparse(text)
        if parsed.hostname:
            return parsed.hostname

    if "/" in text and not text.startswith("/"):
        text = text.split("/", 1)[0]

    return text


def is_network_target(value: str) -> bool:
    if "://" in value:
        return True

    normalized = normalize_host_like(value)
    if not normalized:
        return False

    if normalized.startswith("/dev/"):
        return False
    if normalized.upper().startswith("COM"):
        return False
    if is_ip_address(normalized):
        return True
    if normalized.endswith(".local"):
        return True
    if "." in normalized:
        return True
    return False


def run_command(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True)


def run_command_with_retries(cmd: list[str], retries: int, delay_seconds: float) -> None:
    attempts = max(1, retries)
    for attempt in range(1, attempts + 1):
        try:
            run_command(cmd)
            return
        except subprocess.CalledProcessError:
            if attempt >= attempts:
                raise
            print(f"Upload failed (attempt {attempt}/{attempts}); retrying in {delay_seconds:.1f}s...")
            time.sleep(delay_seconds)


def read_props(device_ip: str, timeout: float) -> dict:
    url = f"http://{device_ip}/props"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            data = json.loads(body)
            if isinstance(data, dict):
                return data
    except Exception as exc:
        print(f"Warning: could not read current props from {url}: {exc}")
    return {}


def post_props(device_ip: str, name: str, room_id: str, location: str, poll_interval: float, timeout: float) -> None:
    # Keep exact key ordering and compact JSON to match simple firmware parser.
    safe_name = name.replace('"', "'")
    safe_room_id = room_id.replace('"', "'")
    safe_location = location.replace('"', "'")
    payload = (
        '{"name":"%s","room_id":"%s","location":"%s","poll_interval":%.1f}'
        % (safe_name, safe_room_id, safe_location, poll_interval)
    )

    req = urllib.request.Request(
        f"http://{device_ip}/props",
        data=payload.encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            print(f"Configured props on {device_ip}: {body}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} while setting props: {detail}") from exc
    except Exception as exc:
        raise RuntimeError(f"Failed to set props on {device_ip}: {exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compile/upload ESP32 camera firmware and configure camera metadata over /props.",
    )
    parser.add_argument("--arduino-cli", default="arduino-cli", help="Path to arduino-cli executable")
    parser.add_argument(
        "--sketch",
        default="esp32cam/CameraWebServer",
        help="Path to sketch directory or .ino file",
    )
    parser.add_argument("--fqbn", default="esp32:esp32:esp32cam", help="Board FQBN")
    parser.add_argument(
        "--build-dir",
        default=".arduino-build/esp32cam",
        help="Output directory for compiled artifacts",
    )
    parser.add_argument("--port", help="Upload port (serial like /dev/ttyUSB0 or OTA IP)")
    parser.add_argument("--protocol", help="Optional upload protocol (pass only if required by your setup)")
    parser.add_argument("--upload-password", help="OTA upload password")
    parser.add_argument("--upload-retries", type=int, default=2, help="Number of upload attempts")
    parser.add_argument("--retry-delay", type=float, default=2.0, help="Seconds to wait between upload retries")
    parser.add_argument("--discovery-timeout", default="8s", help="arduino-cli discovery timeout for upload")

    parser.add_argument("--no-compile", action="store_true", help="Skip compile step")
    parser.add_argument("--no-upload", action="store_true", help="Skip upload step")
    parser.add_argument("--no-config", action="store_true", help="Skip /props configuration step")

    parser.add_argument("--device-ip", help="Device IP for /props configuration (defaults to --port if IP)")
    parser.add_argument("--camera-name", help="Camera name to set in /props")
    parser.add_argument("--room-id", help="Room ID to set in /props")
    parser.add_argument("--location", help="Location to set in /props")
    parser.add_argument("--poll-interval", type=float, help="Poll interval to set in /props")
    parser.add_argument("--http-timeout", type=float, default=5.0, help="HTTP timeout in seconds")

    args = parser.parse_args()

    sketch_path = Path(args.sketch)
    build_dir = Path(args.build_dir)

    if not sketch_path.exists():
        print(f"Sketch path not found: {sketch_path}", file=sys.stderr)
        return 2

    if not args.no_compile:
        build_dir.mkdir(parents=True, exist_ok=True)
        compile_cmd = [
            args.arduino_cli,
            "compile",
            "--fqbn",
            args.fqbn,
            "--board-options",
            f"PartitionScheme=min_spiffs",
            "--output-dir",
            str(build_dir),
            str(sketch_path),
        ]
        run_command(compile_cmd)

    if not args.no_upload:
        if not args.port:
            print("--port is required unless --no-upload is used", file=sys.stderr)
            return 2

        upload_port = normalize_host_like(args.port) or args.port
        upload_protocol = args.protocol

        upload_cmd = [
            args.arduino_cli,
            "upload",
            "--fqbn",
            args.fqbn,
            "--input-dir",
            str(build_dir),
            "--discovery-timeout",
            args.discovery_timeout,
            "-p",
            upload_port,
            str(sketch_path),
        ]
        if upload_protocol:
            upload_cmd.extend(["--protocol", upload_protocol])
        if args.upload_password:
            upload_cmd.extend(["--upload-field", f"password={args.upload_password}"])

        if is_network_target(args.port) and not upload_protocol:
            print("Info: using network upload without explicit --protocol (more reliable for ESP32 OTA).")
        run_command_with_retries(upload_cmd, args.upload_retries, args.retry_delay)

    if not args.no_config:
        device_ip = normalize_host_like(args.device_ip) if args.device_ip else None
        normalized_port = normalize_host_like(args.port) if args.port else None
        if not device_ip and normalized_port and is_network_target(args.port):
            device_ip = normalized_port

        if not device_ip:
            print("Skipping /props config: no --device-ip provided and --port is not an IP")
            return 0

        current = read_props(device_ip, args.http_timeout)
        name = args.camera_name if args.camera_name is not None else str(current.get("name", "cam1"))
        room_id = args.room_id if args.room_id is not None else str(current.get("room_id", "unknown"))
        location = args.location if args.location is not None else str(current.get("location", "unknown"))
        poll_interval = args.poll_interval if args.poll_interval is not None else float(current.get("poll_interval", 10.0))

        post_props(device_ip, name, room_id, location, poll_interval, args.http_timeout)

    print("Done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
