#!/usr/bin/env python3
"""
Gel-controller (Guard-e-loo) - Person Detection Monitor
Monitors ESPHome devices for heartbeat data to detect room occupancy.
"""

import subprocess
import re
import time
import sys
from datetime import datetime
from typing import Optional


class PersonDetector:
    def __init__(self, device_pattern: str = "seeed", config_file: str = "seeedstudio-mr60bha2-kit-8e65b4.yaml"):
        self.device_pattern = device_pattern
        self.config_file = config_file
        self.last_heartbeat_time = None
        self.heartbeat_timeout = 10  # seconds
        self.current_state = None  # None, "occupied", or "empty"
        self.report_interval = 10  # seconds - report status every 10 seconds
        self.last_report_time = None

    def discover_device(self) -> Optional[str]:
        """Discover ESPHome device on the network using avahi-browse."""
        try:
            print("Discovering devices on network...")
            result = subprocess.run(
                ["avahi-browse", "-a", "-t", "-p", "-r"],
                capture_output=True,
                text=True,
                timeout=10
            )

            # Parse avahi-browse output for device names
            lines = result.stdout.split('\n')
            for line in lines:
                if self.device_pattern in line.lower() and '_esphomelib._tcp' in line:
                    # Extract device name from avahi output
                    # Format: =;interface;protocol;name;type;domain;host;...
                    parts = line.split(';')
                    if len(parts) >= 4:
                        device_name = parts[3]
                        print(f"Found device: {device_name}")
                        return device_name

            print(f"No device matching pattern '{self.device_pattern}' found.")
            return None

        except subprocess.TimeoutExpired:
            print("Device discovery timed out.")
            return None
        except FileNotFoundError:
            print("Error: avahi-browse not found. Please install avahi-utils.")
            return None
        except Exception as e:
            print(f"Error during device discovery: {e}")
            return None

    def resolve_device_ip(self, device_name: str) -> Optional[str]:
        """Resolve device hostname to IP address using avahi-resolve."""
        try:
            hostname = f"{device_name}.local"
            print(f"Resolving {hostname}...")

            result = subprocess.run(
                ["avahi-resolve", "-n", hostname],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                # Output format: hostname IP
                match = re.search(r'\s+([\d.]+)$', result.stdout.strip())
                if match:
                    ip = match.group(1)
                    print(f"Resolved to IP: {ip}")
                    return ip

            print(f"Failed to resolve {hostname}")
            return None

        except subprocess.TimeoutExpired:
            print("Device resolution timed out.")
            return None
        except FileNotFoundError:
            print("Error: avahi-resolve not found. Please install avahi-utils.")
            return None
        except Exception as e:
            print(f"Error resolving device: {e}")
            return None

    def update_state(self, new_state: str):
        """Update room state."""
        self.current_state = new_state

    def report_status(self):
        """Report current room status with timestamp."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        status = self.current_state if self.current_state else "unknown"
        if status == "occupied":
            print(f"[{timestamp}] Room occupied")
        elif status == "empty":
            print(f"[{timestamp}] Room empty")
        else:
            print(f"[{timestamp}] Room status: {status}")
        self.last_report_time = time.time()

    def monitor_heartbeat(self, device_ip: str):
        """Monitor ESPHome device logs for heartbeat data."""
        try:
            print(f"Starting heartbeat monitor...")
            print(f"Connecting to device at {device_ip}...")
            print(f"Reporting status every {self.report_interval} seconds")
            print(f"Heartbeat timeout: {self.heartbeat_timeout}s")
            print("-" * 60)

            # Start esphome logs process
            process = subprocess.Popen(
                ["esphome", "logs", self.config_file, "--device", device_ip],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )

            # Pattern to match heartbeat lines
            heartbeat_pattern = re.compile(r"'Real-time heart rate'.*?(\d+\.\d+)\s*bpm")

            # Initialize times
            self.last_report_time = time.time()

            # Monitor output line by line
            for line in iter(process.stdout.readline, ''):
                if not line:
                    break

                current_time = time.time()

                # Check for heartbeat data
                match = heartbeat_pattern.search(line)
                if match:
                    heart_rate = float(match.group(1))
                    self.last_heartbeat_time = current_time

                    # Consider valid heartbeat if rate > 0
                    if heart_rate > 0:
                        self.update_state("occupied")

                # Check if heartbeat timeout exceeded
                if self.last_heartbeat_time is not None:
                    time_since_heartbeat = current_time - self.last_heartbeat_time
                    if time_since_heartbeat > self.heartbeat_timeout:
                        self.update_state("empty")
                        self.last_heartbeat_time = None
                elif self.current_state is None:
                    # Initialize state as empty if no heartbeat detected yet
                    self.update_state("empty")

                # Report status every report_interval seconds
                if current_time - self.last_report_time >= self.report_interval:
                    self.report_status()

            process.wait()

        except FileNotFoundError:
            print("Error: esphome not found. Please install esphome.")
            sys.exit(1)
        except KeyboardInterrupt:
            print("\nMonitoring stopped by user.")
            process.terminate()
            sys.exit(0)
        except Exception as e:
            print(f"Error monitoring device: {e}")
            sys.exit(1)

    def run(self):
        """Main execution flow."""
        print("=" * 60)
        print("Gel-controller (Guard-e-loo) - Person Detection Monitor")
        print("=" * 60)

        # Discover device
        device_name = self.discover_device()
        if not device_name:
            print("Failed to discover device. Exiting.")
            sys.exit(1)

        # Resolve IP
        device_ip = self.resolve_device_ip(device_name)
        if not device_ip:
            print("Failed to resolve device IP. Exiting.")
            sys.exit(1)

        # Monitor heartbeat
        self.monitor_heartbeat(device_ip)


def main():
    detector = PersonDetector()
    detector.run()


if __name__ == "__main__":
    main()
