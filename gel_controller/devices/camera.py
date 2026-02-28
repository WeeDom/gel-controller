import subprocess
import requests
import socket
import os
import ipaddress
from contextlib import contextmanager

HTTP_PORTS = [80, 8080]  # Common ESP32-CAM ports
TIMEOUT = 1.0


def require_root() -> None:
    """Require root privileges for network discovery scans."""
    if os.geteuid() != 0:
        raise PermissionError("Camera discovery must run as root (required for nmap host discovery).")


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
    require_root()
    subnet = detect_local_subnet_24()
    print(f"Scanning {subnet}...")
    result = subprocess.run(
        ["nmap", "-sn", "-T5", "--min-rate", "1000", subnet],
        capture_output=True,
        text=True,
        check=True,
        timeout=30
    )

    ips = []
    for line in result.stdout.splitlines():
        if line.startswith("Nmap scan report for"):
            ip = line.split()[-1].strip("()")
            ips.append(ip)

    return ips


def port_open(ip, port, timeout=TIMEOUT):
    """Check if TCP port is open"""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def is_gel_camera(ip, port):
    """Check if device is a gel-camera by looking for custom header"""
    try:
        response = requests.head(f"http://{ip}:{port}/", timeout=TIMEOUT)
        print(f"    Response headers: {response.headers}")
        # Check for our custom identification header
        device_type = response.headers.get('X-Device-Type', '')
        device_id = response.headers.get('X-Device-Id', '')
        device_name = response.headers.get('X-Device-Name', '')

        if device_type == 'gel-camera':
            return True, device_id, device_name

    except requests.RequestException:
        pass

    return False, None, None


def discover_cameras():
    """Scan network for gel cameras"""
    cameras = []
    scanned_ips = scan_subnet()

    with reduced_privileges_when_possible():
        for ip in scanned_ips:
            print(f"Checking {ip}...")

            for port in HTTP_PORTS:
                if not port_open(ip, port):
                    continue

                is_camera, device_id, device_name = is_gel_camera(ip, port)
                if is_camera:
                    print(f"  ✓ Camera found! MAC: {device_id}, Name: {device_name}")
                    cameras.append({
                        "ip": ip,
                        "port": port,
                        "mac": device_id,
                        "name": device_name,
                        "url": f"http://{ip}:{port}",
                        "stream_url": f"http://{ip}:{port}:81/stream"  # ESP32-CAM stream port
                    })
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
