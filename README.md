# Gel-controller (Guard-e-loo)

Person detection monitor for ESPHome devices with MR60BHA2 radar sensors.

## Features

- Automatically discovers ESPHome devices on the local network
- Resolves device IP addresses
- Monitors real-time heartbeat data from MR60BHA2 radar sensor
- Outputs room occupancy status:
  - "Room occupied" when heartbeat is detected
  - "Room empty" when no heartbeat detected for 10 seconds

## Requirements

- Python 3.8+
- Avahi utilities (for device discovery)
- ESPHome

### Install System Dependencies

```bash
# Ubuntu/Debian
sudo apt-get install avahi-utils avahi-daemon

# macOS
# Avahi (Bonjour) is built-in
```

### Install Python Dependencies

```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Usage

### Run the detector

```bash
./detect_person.py
```

Or:

```bash
python3 detect_person.py
```

The script will:
1. Discover ESPHome devices on the network (looking for "seeed" devices)
2. Resolve the device IP address
3. Connect to the device and monitor heartbeat data
4. Output occupancy status changes

### Capture baseline on demand

When the controller is running, you can trigger immediate baseline captures from another terminal:

```bash
source venv/bin/activate
python3 capture_baseline.py
```

Target a specific room:

```bash
python3 capture_baseline.py --room-id 101
```

This sends a local command to the controller (`POST /capture-baseline` on `127.0.0.1:8765`).

Successful baseline captures are recorded in SQLite at `logs/baselines.db` in table `baselines` with:
- `camera_name`
- `captured_at`
- `location`

### Build/flash ESP32 camera without Arduino GUI

Use the headless deploy helper (requires `arduino-cli`):

```bash
source venv/bin/activate
python3 deploy_esp32_camera.py \
  --port 10.42.0.57 \
  --protocol network \
  --camera-name cam2 \
  --room-id kitchen \
  --location downstairs
```

This will:
1. Compile `esp32cam/CameraWebServer`
2. Upload firmware to the given port/IP
3. Configure `/props` on the camera with `name`, `room_id`, `location` (and optional `--poll-interval`)

Useful flags:
- `--no-upload` compile only
- `--no-compile` upload existing build output
- `--device-ip <ip>` configure a different device than the upload port

### Example Output

```
============================================================
Gel-controller (Guard-e-loo) - Person Detection Monitor
============================================================
Discovering devices on network...
Found device: seeedstudio-mr60bha2-kit-8e65b4
Resolving seeedstudio-mr60bha2-kit-8e65b4.local...
Resolved to IP: 192.168.1.189
Starting heartbeat monitor...
Connecting to device at 192.168.1.189...
Waiting for heartbeat data (timeout: 10s)
------------------------------------------------------------
[2026-01-30 14:58:02] Room occupied
[2026-01-30 14:59:15] Room empty
```

## Configuration

The device configuration is stored in `seeedstudio-mr60bha2-kit-8e65b4.yaml`.

WiFi credentials are stored in `secrets.yaml`:

```yaml
wifi_ssid: "YourWiFiSSID"
wifi_password: "YourWiFiPassword"
```

## How It Works

1. **Device Discovery**: Uses `avahi-browse` to find ESPHome devices on the local network
2. **IP Resolution**: Uses `avahi-resolve` to get the device's IP address
3. **Log Monitoring**: Connects to the device using `esphome logs` command
4. **Heartbeat Detection**: Parses log output for "Real-time heart rate" sensor data
5. **State Tracking**:
   - Marks room as occupied when heartbeat detected (heart rate > 0)
   - Marks room as empty after 10 seconds without heartbeat detection

## Troubleshooting

### Device not found
- Ensure the device is powered on and connected to WiFi
- Check that avahi-daemon is running: `sudo systemctl status avahi-daemon`
- Try manual discovery: `avahi-browse -a | grep -i esp`

### Cannot resolve IP
- Verify the device hostname: `avahi-resolve -n seeedstudio-mr60bha2-kit-8e65b4.local`
- Check network connectivity

### ESPHome connection fails
- Verify the IP address is correct
- Test manual connection: `esphome logs seeedstudio-mr60bha2-kit-8e65b4.yaml --device <IP>`
- Check firewall settings

## License

MIT
