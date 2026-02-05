import subprocess
import requests
import socket

SUBNET = "10.42.0.0/24"
HTTP_PORTS = [80, 8080]  # Common ESP32-CAM ports
TIMEOUT = 1.0


def scan_subnet():
    """Fast scan for live IPs on local subnet"""
    print(f"Scanning {SUBNET}...")
    result = subprocess.run(
        ["sudo", "nmap", "-sn", "-T5", "--min-rate", "1000", SUBNET],
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

    for ip in scan_subnet():
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
