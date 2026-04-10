#!/usr/bin/env python3
import argparse
import ipaddress
import json
import re
import subprocess
import sys
import time
import socket
import urllib.error
import urllib.request
from urllib.parse import urlparse
from pathlib import Path
from gel_controller.camera_auth import signed_url_and_headers
from gel_controller.devices.camera import discover_cameras

CAM_MODE_ALIASES = {
    "room": "room",
    "room_cam": "room",
    "room-camera": "room",
    "door": "door",
    "doorway": "door",
    "door_cam": "door",
    "door-camera": "door",
    "unknown": "room",
}


def infer_target_from_sketch(sketch_path: Path) -> str:
    text = str(sketch_path).replace("\\", "/").lower()
    if "esp32cam-door" in text or "doorcamera" in text:
        return "door"
    return "camera"


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


def run_command_capture(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(cmd))
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", file=sys.stderr)
    return result


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


def run_command_with_retries_capture(cmd: list[str], retries: int, delay_seconds: float) -> subprocess.CompletedProcess[str]:
    attempts = max(1, retries)
    for attempt in range(1, attempts + 1):
        try:
            return run_command_capture(cmd)
        except subprocess.CalledProcessError:
            if attempt >= attempts:
                raise
            print(f"Upload failed (attempt {attempt}/{attempts}); retrying in {delay_seconds:.1f}s...")
            time.sleep(delay_seconds)

    raise RuntimeError("Unreachable retry state")


def normalize_cam_mode(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = CAM_MODE_ALIASES.get(value.strip().lower())
    if normalized is None:
        valid = ", ".join(sorted(set(CAM_MODE_ALIASES.values())))
        raise ValueError(f"Unsupported --cam-mode '{value}'. Expected one of: {valid}")
    return normalized


def wait_for_http_ready(device_ip: str, retries: int, delay_seconds: float, timeout: float) -> None:
    attempts = max(1, retries)
    status_url = f"http://{device_ip}/pair/status"

    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(status_url, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as response:
                if response.status == 200:
                    return
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, ConnectionRefusedError):
                pass
            elif isinstance(reason, socket.timeout):
                pass
            elif attempt >= attempts:
                raise RuntimeError(f"Camera HTTP not ready at {status_url}: {exc}") from exc
        except Exception as exc:
            if attempt >= attempts:
                raise RuntimeError(f"Camera HTTP not ready at {status_url}: {exc}") from exc

        if attempt < attempts:
            time.sleep(delay_seconds)

    raise RuntimeError(f"Camera HTTP not ready at {status_url} after {attempts} attempts")


def camera_identity(camera: dict) -> str:
    mac = str(camera.get("mac", "")).strip().lower()
    if mac:
        return f"mac:{mac}"
    ip = str(camera.get("ip", "")).strip()
    if ip:
        return f"ip:{ip}"
    return ""


def auto_discover_device_ip(
    retries: int,
    delay_seconds: float,
    known_camera_keys: set[str] | None = None,
    expected_mac: str | None = None,
) -> str:
    attempts = max(1, retries)
    known_camera_keys = known_camera_keys or set()
    expected_mac = expected_mac.strip().lower() if expected_mac else None

    for attempt in range(1, attempts + 1):
        cameras = discover_cameras()
        if cameras:
            if expected_mac:
                for camera in cameras:
                    mac = str(camera.get("mac", "")).strip().lower()
                    if mac == expected_mac:
                        ip = str(camera.get("ip", "")).strip()
                        if ip:
                            print(f"Auto-discovered flashed camera by MAC at {ip}")
                            return ip

                visible_summary = ", ".join(
                    f"{camera.get('name', 'unknown')}@{camera.get('ip', '?')}[{camera.get('mac', '?')}]"
                    for camera in cameras
                )
                if attempt >= attempts:
                    raise RuntimeError(
                        "Could not find the flashed camera on the LAN after upload. "
                        f"Expected MAC {expected_mac}, visible cameras: {visible_summary or 'none'}"
                    )

                time.sleep(delay_seconds)
                continue

            new_cameras = [camera for camera in cameras if camera_identity(camera) not in known_camera_keys]
            candidates = new_cameras or cameras

            if len(candidates) == 1:
                ip = str(candidates[0].get("ip", "")).strip()
                if ip:
                    print(f"Auto-discovered camera at {ip}")
                    return ip

            candidate_summary = ", ".join(
                f"{camera.get('name', 'unknown')}@{camera.get('ip', '?')}"
                for camera in candidates
            )
            if attempt >= attempts:
                raise RuntimeError(
                    "Could not uniquely identify camera IP after upload. "
                    f"Candidates: {candidate_summary or 'none'}"
                )

        if attempt < attempts:
            time.sleep(delay_seconds)

    raise RuntimeError("Could not auto-discover camera IP after upload")


def extract_mac_from_upload_output(output: str) -> str | None:
    match = re.search(r"\bMAC:\s*([0-9A-Fa-f:]{17})\b", output)
    if not match:
        return None
    return match.group(1).lower()


def read_props(device_ip: str, timeout: float) -> dict:
    url, headers = signed_url_and_headers(
        base_url=f"http://{device_ip}",
        path="/props",
        method="GET",
    )
    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            data = json.loads(body)
            if isinstance(data, dict):
                return data
    except Exception as exc:
        print(f"Warning: could not read current props from {url}: {exc}")
    return {}


def post_props(
    device_ip: str,
    name: str,
    room_id: str,
    location: str,
    cam_mode: str,
    poll_interval: float,
    timeout: float,
) -> None:
    payload = json.dumps(
        {
            "name": name.replace('"', "'"),
            "room_id": room_id.replace('"', "'"),
            "location": location.replace('"', "'"),
            "cam_mode": cam_mode.replace('"', "'"),
            "poll_interval": poll_interval,
        },
        separators=(",", ":"),
    )

    url, headers = signed_url_and_headers(
        base_url=f"http://{device_ip}",
        path="/props",
        method="POST",
        extra_headers={"Content-Type": "application/json"},
    )

    req = urllib.request.Request(
        url,
        data=payload.encode("utf-8"),
        headers=headers,
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


def verify_props(
    device_ip: str,
    expected_name: str,
    expected_room_id: str,
    expected_location: str,
    expected_cam_mode: str,
    expected_poll_interval: float,
    timeout: float,
) -> None:
    current = read_props(device_ip, timeout)
    mismatches = []

    if str(current.get("name", "")) != expected_name:
        mismatches.append(f"name={current.get('name')!r}")
    if str(current.get("room_id", "")) != expected_room_id:
        mismatches.append(f"room_id={current.get('room_id')!r}")
    if str(current.get("location", "")) != expected_location:
        mismatches.append(f"location={current.get('location')!r}")
    if str(current.get("cam_mode", "")) != expected_cam_mode:
        mismatches.append(f"cam_mode={current.get('cam_mode')!r}")

    try:
        current_poll = float(current.get("poll_interval"))
    except (TypeError, ValueError):
        current_poll = None
    if current_poll is None or abs(current_poll - expected_poll_interval) > 0.05:
        mismatches.append(f"poll_interval={current.get('poll_interval')!r}")

    if mismatches:
        raise RuntimeError(
            f"Camera at {device_ip} did not persist the requested props. "
            f"Mismatched values: {', '.join(mismatches)}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compile/upload ESP32 camera firmware for camera or door-camera targets.",
    )
    parser.add_argument("--arduino-cli", default="arduino-cli", help="Path to arduino-cli executable")
    parser.add_argument(
        "--target",
        choices=["auto", "camera", "door"],
        default="auto",
        help="Deployment profile: camera, door, or auto-detect from --sketch",
    )
    parser.add_argument(
        "--sketch",
        default=None,
        help="Path to sketch directory or .ino file",
    )
    parser.add_argument("--fqbn", default="esp32:esp32:esp32cam", help="Board FQBN")
    parser.add_argument(
        "--build-dir",
        default=None,
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
    parser.add_argument(
        "--config-mode",
        choices=["auto", "props", "none"],
        default="auto",
        help="Post-upload config behavior: auto (by target), props, or none",
    )

    parser.add_argument("--device-ip", help="Device IP for /props configuration (defaults to --port if IP)")
    parser.add_argument("--camera-name", help="Camera name to set in /props")
    parser.add_argument("--room-id", help="Room ID to set in /props")
    parser.add_argument("--location", help="Location to set in /props")
    parser.add_argument(
        "--cam-mode",
        help="Camera mode to set in /props (`room` or `door`)",
    )
    parser.add_argument("--poll-interval", type=float, help="Poll interval to set in /props")
    parser.add_argument("--http-timeout", type=float, default=5.0, help="HTTP timeout in seconds")
    parser.add_argument("--http-retries", type=int, default=12, help="HTTP readiness/config retry attempts")
    parser.add_argument("--http-retry-delay", type=float, default=2.0, help="Seconds between HTTP retries")

    args = parser.parse_args()

    if args.sketch:
        sketch_path = Path(args.sketch)
    elif args.target == "door":
        sketch_path = Path("esp32cam-door/DoorCamera")
    else:
        sketch_path = Path("esp32cam/CameraWebServer")

    target = args.target if args.target != "auto" else infer_target_from_sketch(sketch_path)

    if args.build_dir:
        build_dir = Path(args.build_dir)
    elif target == "door":
        build_dir = Path(".arduino-build/esp32cam-door")
    else:
        build_dir = Path(".arduino-build/esp32cam")

    if args.config_mode == "auto":
        config_mode = "props" if target == "camera" else "none"
    else:
        config_mode = args.config_mode

    if args.no_config:
        config_mode = "none"

    print(f"Target profile: {target}")
    print(f"Sketch: {sketch_path}")
    print(f"Build dir: {build_dir}")
    print(f"Config mode: {config_mode}")

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

    known_camera_keys: set[str] = set()
    should_auto_discover_ip = (
        not args.no_config
        and not args.device_ip
        and bool(args.port)
        and not is_network_target(args.port)
    )

    if should_auto_discover_ip:
        try:
            known_camera_keys = {
                identity
                for camera in discover_cameras()
                if (identity := camera_identity(camera))
            }
            if known_camera_keys:
                print(f"Found {len(known_camera_keys)} existing camera(s) before upload")
        except Exception as exc:
            print(f"Warning: pre-upload camera discovery failed: {exc}")

    flashed_device_mac: str | None = None

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
        upload_result = run_command_with_retries_capture(upload_cmd, args.upload_retries, args.retry_delay)
        flashed_device_mac = extract_mac_from_upload_output((upload_result.stdout or "") + "\n" + (upload_result.stderr or ""))
        if flashed_device_mac:
            print(f"Flashed device MAC: {flashed_device_mac}")

    if config_mode == "props":
        device_ip = normalize_host_like(args.device_ip) if args.device_ip else None
        normalized_port = normalize_host_like(args.port) if args.port else None
        if not device_ip and normalized_port and is_network_target(args.port):
            device_ip = normalized_port

        if not device_ip:
            print("No camera IP provided; attempting LAN auto-discovery...")
            device_ip = auto_discover_device_ip(
                retries=args.http_retries,
                delay_seconds=args.http_retry_delay,
                known_camera_keys=known_camera_keys,
                expected_mac=flashed_device_mac,
            )

        print(f"Waiting for camera HTTP to become ready at {device_ip}...")
        wait_for_http_ready(
            device_ip=device_ip,
            retries=args.http_retries,
            delay_seconds=args.http_retry_delay,
            timeout=args.http_timeout,
        )

        current = read_props(device_ip, args.http_timeout)
        name = args.camera_name if args.camera_name is not None else str(current.get("name", "cam1"))
        room_id = args.room_id if args.room_id is not None else str(current.get("room_id", "unknown"))
        location = args.location if args.location is not None else str(current.get("location", "unknown"))
        current_cam_mode = normalize_cam_mode(str(current.get("cam_mode", "room")))
        cam_mode = normalize_cam_mode(args.cam_mode) if args.cam_mode is not None else current_cam_mode
        poll_interval = args.poll_interval if args.poll_interval is not None else float(current.get("poll_interval", 10.0))
        post_props(device_ip, name, room_id, location, cam_mode or "room", poll_interval, args.http_timeout)
        verify_props(device_ip, name, room_id, location, cam_mode or "room", poll_interval, args.http_timeout)

    print("Done")
    return 0


if __name__ == "__main__":
    print("""
ESP32 Camera Deployment Script

This script compiles and uploads ESP32 camera firmware using arduino-cli, then configures camera metadata over HTTP.

Bricked the device? Plugged it into the USB port?

Change --port to the serial port (probably /dev/ttyUSB0 or COM3)
""")
    sys.exit(main())
