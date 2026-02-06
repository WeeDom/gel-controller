#! /usr/bin/env python3

import asyncio
import logging
from time import sleep, time
from gel_controller.devices.pir import discover_presence_sensors

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

"""GEL Controller: Presence Sensor Discovery and Heartbeat Monitor."""

async def monitor_heartbeat_sensor(host: str, port: int = 6053):
    """
    Connect to ESPHome device and monitor heartbeat sensor.

    Args:
        host: Device IP address
        port: ESPHome API port (default: 6053)
    """
    from aioesphomeapi.client import APIClient

    logger.info(f"Connecting to {host}:{port}...")

    api_client = APIClient(host, port, None)

    try:
        await api_client.connect(login=True)
        logger.info(f"✓ Connected to {host}")

        # List all entities
        entities, services = await api_client.list_entities_services()
        logger.info(f"Found {len(entities)} entities:")

        heartbeat_sensor_key = None
        for entity in entities:
            logger.info(f"  - {entity.name} (key: {entity.key}, type: {type(entity).__name__})")
            if hasattr(entity, 'name') and 'heart rate' in entity.name.lower():
                heartbeat_sensor_key = entity.key
                logger.info(f"    ❤️  Found heartbeat sensor! Key: {entity.key}")

        if heartbeat_sensor_key is None:
            logger.warning("No heartbeat sensor found")
            return

        # Track heartbeat state
        last_heartbeat_time = None
        heartbeat_timeout = 10.0  # seconds

        def handle_state_change(state):
            nonlocal last_heartbeat_time

            if state.key == heartbeat_sensor_key:
                heart_rate = float(state.state)

                if heart_rate > 0:
                    last_heartbeat_time = time()
                    logger.info(f"💓 Heartbeat detected: {heart_rate} bpm → OCCUPIED")
                else:
                    logger.debug(f"Heart rate: {heart_rate} bpm (no heartbeat)")

        # Subscribe to state changes
        api_client.subscribe_states(handle_state_change)
        logger.info("✓ Subscribed to state changes. Monitoring...")

        # Monitor for heartbeat timeout
        print("\n🔍 Monitoring for presence (Ctrl+C to stop)...\n")
        while True:
            if last_heartbeat_time is not None:
                time_since_heartbeat = time() - last_heartbeat_time
                if time_since_heartbeat > heartbeat_timeout:
                    logger.info(f"⏱️  Timeout after {time_since_heartbeat:.1f}s → EMPTY")
                    last_heartbeat_time = None

            await asyncio.sleep(1)

    except KeyboardInterrupt:
        logger.info("\n\n🛑 Stopping monitor...")
    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        await api_client.disconnect()
        logger.info("✓ Disconnected")


async def main():
    """Main function to discover sensors and monitor heartbeat."""
    await monitor_heartbeat_sensor(sensor['ip'], sensor['port'])


if __name__ == "__main__":
    # Discover sensors BEFORE entering async context
    print("Discovering presence sensors...")
    sensors = discover_presence_sensors()
    print(f"Found {len(sensors)} presence sensors:\n")

    for sensor in sensors:
        print(f"  📡 {sensor['name']}")
        print(f"     IP: {sensor['ip']}, Port: {sensor['port']}")
        print(f"     Status: {sensor['status']}")

    if not sensors:
        print("No sensors found!")
    else:
        # Monitor the first sensor found
        sensor = sensors[0]
        print(f"\n🎯 Monitoring {sensor['name']} at {sensor['ip']}:{sensor['port']}\n")
        asyncio.run(main())