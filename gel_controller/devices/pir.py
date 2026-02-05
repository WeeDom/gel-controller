import asyncio
import subprocess
import socket
import time
from aioesphomeapi.client import APIClient

ESPRESSIF_OUIS = {
    "84:1f:e8",
    "24:6f:28",
    "7c:df:a1",
    "30:ae:a4",
}

SUBNET = "10.42.0.0/24"  # Your WiFi hotspot network
ESPHOME_PORT = 6053

# Known presence sensor devices (always check these first)
KNOWN_SENSORS = [
    {"host": "10.42.0.156", "port": 6053, "name": "seeedstudio-mr60bha2-kit-8e65b4"}
]


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

    devices = []
    current_ip = None
    for line in result.stdout.splitlines():
        if line.startswith("Nmap scan report for"):
            current_ip = line.split()[-1].strip("()")
        elif "MAC Address:" in line and current_ip:
            mac = line.split("MAC Address: ")[1].split()[0].lower()
            devices.append((current_ip, mac))
            current_ip = None

    return devices


def is_espressif(mac):
    """Check if MAC is from Espressif"""
    if not mac:
        return False
    return mac[:8] in ESPRESSIF_OUIS


def port_open(ip, port, timeout=1.0):
    """Check if TCP port is open"""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


async def is_presence_sensor(host, port=6053):
    """
    Check if an ESPHome device is a presence sensor by connecting and inspecting its entities.
    """
    api = APIClient(host, port, None)
    try:
        await asyncio.wait_for(api.connect(login=True), timeout=10.0)

        # List all entities - returns (entities, services) tuple
        entities, services = await api.list_entities_services()

        # Look for binary sensors with presence-related names
        presence_keywords = ["person", "presence", "occupancy", "pir", "motion", "target", "detected"]

        for entity in entities:
            entity_type = type(entity).__name__
            if "BinarySensor" in entity_type:
                name = entity.name.lower()
                if any(keyword in name for keyword in presence_keywords):
                    return True

        return False
    except Exception as e:
        print(f"  Error connecting to {host}: {e}")
        return False
    finally:
        try:
            await api.disconnect()
        except:
            pass


def discover_presence_sensors():
    """
    Discover presence sensors from:
    1. Known configured devices (check first)
    2. Network scan for Espressif devices with ESPHome API
    """
    sensors = []
    seen_ips = set()

    # First, check known devices
    print("=== Checking known devices ===")
    for device in KNOWN_SENSORS:
        host = device["host"]
        port = device["port"]
        name = device["name"]

        print(f"Checking {name} @ {host}:{port}...")

        if asyncio.run(is_presence_sensor(host, port)):
            print(f"  ✓ Presence sensor confirmed!")
            sensors.append({
                "name": name,
                "ip": host,
                "port": port,
                "status": "idle",
                "last_seen": time.time()
            })
            seen_ips.add(host)
        else:
            print(f"  Could not connect or verify sensor.")

    # Then scan local network for new devices
    print("\n=== Scanning local network ===")
    for ip, mac in scan_subnet():
        if ip in seen_ips:
            continue

        print(f"Found device IP={ip} MAC={mac}")

        if not is_espressif(mac):
            print("  Not an Espressif device, skipping.")
            continue

        if not port_open(ip, ESPHOME_PORT):
            print(f"  Port {ESPHOME_PORT} not open, skipping.")
            continue

        print("  Espressif device with ESPHome API, checking if presence sensor...")

        if asyncio.run(is_presence_sensor(ip, ESPHOME_PORT)):
            print(f"  ✓ New presence sensor discovered!")
            sensors.append({
                "name": f"sensor-{mac.replace(':', '')}",
                "ip": ip,
                "port": ESPHOME_PORT,
                "mac": mac,
                "status": "idle",
                "last_seen": time.time()
            })
            seen_ips.add(ip)
        else:
            print("  Not a presence sensor, skipping.")
    return sensors


if __name__ == "__main__":
    import json
    print("🔍 Discovering presence sensors...\n")
    sensors = discover_presence_sensors()
    print("\n" + "="*50)
    print("DISCOVERED SENSORS")
    print("="*50)
    if sensors:
        print(json.dumps(sensors, indent=2))
    else:
        print("No presence sensors found.")
