import asyncio
import time
from aioesphomeapi import APIClient

# Known presence sensor device
KNOWN_SENSORS = [
    {"host": "192.168.1.189", "port": 6053, "name": "seeedstudio-mr60bha2-kit-8e65b4"}
]


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
    """Check known presence sensor devices."""
    sensors = []

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
        else:
            print(f"  Could not connect or verify sensor.")

    return sensors
