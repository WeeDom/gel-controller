import subprocess
import requests
import socket
import os
import ipaddress
import time
from contextlib import contextmanager
from gel_controller.camera_auth import signed_url_and_headers

HTTP_PORTS = [80, 8080]  # Common ESP32-CAM ports
TIMEOUT = float(os.getenv("GEL_CAMERA_DISCOVERY_TIMEOUT", "1.8"))
PROBE_RETRIES = int(os.getenv("GEL_CAMERA_DISCOVERY_PROBE_RETRIES", "4"))
SCAN_RETRIES = int(os.getenv("GEL_CAMERA_DISCOVERY_SCAN_RETRIES", "2"))
REQUIRE_OTA = os.getenv("GEL_REQUIRE_OTA", "1").lower() not in {"0", "false", "no"}


def detect_local_subnet_24() -> str:
    """Detect active local interface subnet and return /24 network to scan."""
    try:
        route = subprocess.run(
            ["ip", "route", "get", "1.1.1.1"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()

        # Typical output contains: "src <ip>"
        parts = route.split()
        if "src" in parts:
            src_ip = parts[parts.index("src") + 1]
        else:
            src_ip = socket.gethostbyname(socket.gethostname())

        ip = ipaddress.ip_address(src_ip)
        if ip.version == 4:
            return str(ipaddress.ip_network(f"{src_ip}/24", strict=False))
    except Exception:
        pass

    return "10.42.0.0/24"


@contextmanager
def reduced_privileges_when_possible():
    """Temporarily drop euid/egid to invoking user when running under sudo."""
    if os.geteuid() != 0:
        yield
        return

    sudo_uid = os.environ.get("SUDO_UID")
    sudo_gid = os.environ.get("SUDO_GID")
    if not sudo_uid or not sudo_gid:
        yield
        return

    original_euid = os.geteuid()
    original_egid = os.getegid()
    dropped = False

    try:
        os.setegid(int(sudo_gid))
        os.seteuid(int(sudo_uid))
        dropped = True
    except OSError:
        dropped = False

    try:
        yield
    finally:
        if dropped:
            os.seteuid(original_euid)
            os.setegid(original_egid)


def scan_subnet():
    """Scan for candidate IPv4 hosts using nmap and neighbor cache."""
    subnet = detect_local_subnet_24()
    print(f"Scanning {subnet}...")
    network = ipaddress.ip_network(subnet, strict=False)
    candidates = set()

    for _ in range(max(1, SCAN_RETRIES)):
        try:
            result = subprocess.run(
                ["nmap", "-sn", subnet],
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            )
        except (subprocess.SubprocessError, FileNotFoundError):
            continue

        for line in result.stdout.splitlines():
            if line.startswith("Nmap scan report for"):
                ip_text = line.split()[-1].strip("()")
                try:
                    ip_obj = ipaddress.ip_address(ip_text)
                except ValueError:
                    continue
                if ip_obj in network:
                    candidates.add(ip_text)

    try:
        neigh = subprocess.run(
            ["ip", "neigh", "show"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.splitlines()
        for line in neigh:
            parts = line.split()
            if not parts:
                continue
            ip_text = parts[0]
            try:
                ip_obj = ipaddress.ip_address(ip_text)
            except ValueError:
                continue
            if ip_obj.version != 4 or ip_obj not in network:
                continue
            if any(state in parts for state in ("FAILED", "INCOMPLETE")):
                continue
            candidates.add(ip_text)
    except (subprocess.SubprocessError, FileNotFoundError):
        pass

    def sort_key(ip_text: str):
        return int(ipaddress.ip_address(ip_text))

    ips = sorted(candidates, key=sort_key)
    return ips


def is_camera_status_payload(payload):
    """Heuristic for identifying camera /status payloads when headers are missing."""
    if not isinstance(payload, dict):
        return False
    expected_keys = {"framesize", "pixformat", "quality", "ota_enabled", "firmware_version"}
    return any(key in payload for key in expected_keys)


def fetch_status_payload(ip, port):
    try:
        url, headers = signed_url_and_headers(
            base_url=f"http://{ip}:{port}",
            path="/status",
            method="GET",
        )
        response = requests.get(url, timeout=TIMEOUT, headers=headers)
        if response.status_code != 200:
            return None
        payload = response.json()
        return payload if isinstance(payload, dict) else None
    except (requests.RequestException, ValueError):
        return None


def normalize_ota_flag(value):
    """Normalize ota_enabled from payload into True/False/None (unknown)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    return None


def read_camera_props(ip, port):
    """Fetch camera metadata from /props after a successful identity probe."""
    try:
        url, headers = signed_url_and_headers(
            base_url=f"http://{ip}:{port}",
            path="/props",
            method="GET",
        )
        response = requests.get(url, timeout=TIMEOUT, headers=headers)
        if response.status_code != 200:
            return {}
        data = response.json()
        return data if isinstance(data, dict) else {}
    except (requests.RequestException, ValueError):
        return {}


def read_camera_capabilities(ip, port):
    """Read OTA capability from health/status endpoints if available."""
    capabilities = {}
    for path in ("/health", "/status"):
        try:
            url, headers = signed_url_and_headers(
                base_url=f"http://{ip}:{port}",
                path=path,
                method="GET",
            )
            response = requests.get(url, timeout=TIMEOUT, headers=headers)
            if response.status_code != 200:
                continue
            payload = response.json()
            if isinstance(payload, dict):
                capabilities.update(payload)
        except (requests.RequestException, ValueError):
            continue

    return capabilities


def probe_camera(ip, port):
    """Probe for a GEL camera and return its metadata when identified."""
    for attempt in range(PROBE_RETRIES):
        response = None
        try:
            url, headers = signed_url_and_headers(
                base_url=f"http://{ip}:{port}",
                path="/",
                method="HEAD",
            )
            response = requests.head(url, timeout=TIMEOUT, headers=headers)
        except requests.RequestException:
            response = None

        # Primary identity is via custom header from GET/HEAD /.
        device_type = response.headers.get("X-Device-Type", "") if response else ""
        status_payload = None
        if device_type != "gel-camera":
            # Fallback: some transient states may miss headers; verify with /status signature.
            status_payload = fetch_status_payload(ip, port)
            if not is_camera_status_payload(status_payload):
                if attempt < PROBE_RETRIES - 1:
                    time.sleep(0.2)
                continue

        try:
            props = read_camera_props(ip, port)
            capabilities = read_camera_capabilities(ip, port)
            if status_payload:
                capabilities.update(status_payload)
            identity_headers = response.headers if response is not None else {}
            device_id = identity_headers.get("X-Device-ID", "")
            device_name = props.get("name") or identity_headers.get("X-Device-Name", f"camera-{ip}")
            room_id = props.get("room_id") or identity_headers.get("X-Room-ID", "unknown")
            cam_mode = props.get("cam_mode") or identity_headers.get("X-Cam-Mode", "room")
            firmware_version = capabilities.get("firmware_version") or identity_headers.get("X-Firmware-Version", "unknown")
            ota_enabled = normalize_ota_flag(capabilities.get("ota_enabled"))
            location = props.get("location", "unknown")
            poll_interval = props.get("poll_interval", 10.0)
            try:
                poll_interval = float(poll_interval)
            except (TypeError, ValueError):
                poll_interval = 10.0

            return {
                "ip": ip,
                "port": port,
                "mac": device_id,
                "name": device_name,
                "room_id": room_id,
                "cam_mode": cam_mode,
                "location": location,
                "poll_interval": poll_interval,
                "ota_enabled": ota_enabled,
                "firmware_version": str(firmware_version),
                "url": f"http://{ip}:{port}",
                "stream_url": f"http://{ip}:81/stream",
            }

        except requests.RequestException:
            pass

        # Small backoff helps catch devices mid-boot after reset.
        if attempt < PROBE_RETRIES - 1:
            time.sleep(0.2)

    return None


def discover_cameras():
    """Scan network for gel cameras"""
    cameras = []
    scanned_ips = scan_subnet()
    print(f"Found {len(scanned_ips)} active IPs. Probing for cameras...")

    with reduced_privileges_when_possible():
        for ip in scanned_ips:
            print(f"Checking {ip}...")

            for port in HTTP_PORTS:
                camera = probe_camera(ip, port)
                if camera:
                    if REQUIRE_OTA and camera.get("ota_enabled") is not True:
                        ota_state = camera.get("ota_enabled")
                        ota_reason = "missing_or_unreadable" if ota_state is None else "false"
                        print(
                            f"  ! Camera at {camera['ip']} rejected: ota_enabled={ota_reason} "
                            f"(fw={camera.get('firmware_version', 'unknown')}) "
                            "(set GEL_REQUIRE_OTA=0 to allow)"
                        )
                        break

                    ota_state_text = "unknown" if camera.get("ota_enabled") is None else str(camera.get("ota_enabled"))
                    print(
                        f"  ✓ Camera found! MAC: {camera['mac']}, Name: {camera['name']}, "
                        f"Room: {camera['room_id']}, Mode: {camera['cam_mode']}, OTA: {ota_state_text}, "
                        f"FW: {camera['firmware_version']}"
                    )
                    cameras.append(camera)
                    break  # Found it, no need to check other ports

    return cameras


if __name__ == "__main__":
    import json
    print("📷 Discovering cameras...\n")
    cameras = discover_cameras()
    print("\n" + "="*50)
    print("DISCOVERED CAMERAS")
    print("="*50)
    if cameras:
        print(json.dumps(cameras, indent=2))
    else:
        print("No cameras found.")
