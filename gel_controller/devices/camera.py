import subprocess
import requests
import socket
import os
import ipaddress
import time
from contextlib import contextmanager
from gel_controller.camera_auth import signed_url_and_headers

HTTP_PORTS = [80, 8080]  # Common ESP32-CAM ports
TIMEOUT = 1.0
PROBE_RETRIES = 3
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
    """Fast scan for live IPs on local subnet"""
    subnet = detect_local_subnet_24()
    print(f"Scanning {subnet}...")
    result = subprocess.run(
        ["nmap", "-sn", subnet],
        capture_output=True,
        text=True,
        check=True,
        timeout=60
    )

    ips = []
    for line in result.stdout.splitlines():
        if line.startswith("Nmap scan report for"):
            ip = line.split()[-1].strip("()")
            ips.append(ip)

    return ips


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
        try:
            url, headers = signed_url_and_headers(
                base_url=f"http://{ip}:{port}",
                path="/",
                method="HEAD",
            )
            response = requests.head(url, timeout=TIMEOUT, headers=headers)

            # Check for our custom identification header.
            device_type = response.headers.get("X-Device-Type", "")
            if device_type != "gel-camera":
                return None

            props = read_camera_props(ip, port)
            capabilities = read_camera_capabilities(ip, port)
            device_id = response.headers.get("X-Device-ID", "")
            device_name = props.get("name") or response.headers.get("X-Device-Name", f"camera-{ip}")
            room_id = props.get("room_id") or response.headers.get("X-Room-ID", "unknown")
            cam_mode = props.get("cam_mode") or response.headers.get("X-Cam-Mode", "room")
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
                "ota_enabled": bool(capabilities.get("ota_enabled", False)),
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
    print(f"Found {scanned_ips} active IPs. Probing for cameras...")

    with reduced_privileges_when_possible():
        for ip in scanned_ips:
            print(f"Checking {ip}...")

            for port in HTTP_PORTS:
                camera = probe_camera(ip, port)
                if camera:
                    if REQUIRE_OTA and not camera.get("ota_enabled", False):
                        print(
                            f"  ! Camera at {camera['ip']} rejected: ota_enabled=false "
                            "(set GEL_REQUIRE_OTA=0 to allow)"
                        )
                        break

                    print(
                        f"  ✓ Camera found! MAC: {camera['mac']}, Name: {camera['name']}, "
                        f"Room: {camera['room_id']}, Mode: {camera['cam_mode']}, OTA: {camera['ota_enabled']}"
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
