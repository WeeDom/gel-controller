# ESP32-CAM OTA Update Setup

## Overview
OTA (Over-The-Air) updates allow you to upload new firmware to the ESP32-CAM over WiFi without needing to connect it via USB.

## Critical Rule
Arduino IDE sucks. Don't use it for this project.

Use `arduino-cli` or the repo helper scripts so the board target remains `AI Thinker ESP32-CAM` (`esp32:esp32:esp32cam`) with PSRAM enabled. Using Arduino IDE made it too easy to accidentally flash `ESP32 Dev Module`, which led to broken Wi-Fi behavior.

## Initial Setup (One-time USB upload required)

### 1. Change Partition Scheme
In the repo workflow, the compile scripts already set the OTA-capable partition scheme:
- `esp32cam-door/utils/door-cam-compile.sh`
- `esp32cam-door/utils/door-cam-upload.sh`

If you are compiling manually, use:
```bash
arduino-cli compile \
  --fqbn esp32:esp32:esp32cam \
  --board-options PartitionScheme=min_spiffs \
  esp32cam-door/DoorCamera
```

### 2. Upload Firmware via USB
Upload the modified firmware once via USB using the repo script or `arduino-cli`. This is required to flash the OTA-enabled firmware.

## Using OTA Updates

### Method 1: Repo helper
Use the appropriate compile/upload script in `esp32cam-door/utils/`, or use `espota.py` directly for OTA once the device is already running OTA-capable firmware.

### Method 2: PlatformIO (if using)
Add to `platformio.ini`:
```ini
upload_protocol = espota
upload_port = <IP_ADDRESS>
upload_flags =
    --auth=B0ll0cks!
```

### Method 3: Command Line (espota.py)
```bash
python ~/.arduino15/packages/esp32/hardware/esp32/2.0.14/tools/espota.py \
  -i <IP_ADDRESS> \
  -p 3232 \
  --auth=B0ll0cks! \
  -f /path/to/firmware.bin
```

## Troubleshooting

### Camera not appearing as network port
1. Check serial monitor - should see "OTA Ready"
2. Verify ESP32-CAM and computer are on same network
3. Check if mDNS is working: `ping esp32cam-1.local`
4. Try using IP address directly instead of hostname

### OTA upload fails
1. Verify password is correct: `B0ll0cks!`
2. Check available flash space (need at least 1.2MB free)
3. Restart the ESP32-CAM
4. Try uploading via USB if OTA continues to fail

### Reverting to Huge APP partition
If you need the larger app space and don't want OTA:
1. Change the compile option away from `PartitionScheme=min_spiffs`
2. Upload via USB (OTA won't work with the larger app partition)
3. Comment out or remove ArduinoOTA code

## Security Notes
- Change the OTA password in the code for production use
- OTA is only available on your local network
- The camera web interface remains unchanged

## Current Configuration
- **Hostname**: `esp32cam-1`
- **OTA Password**: `B0ll0cks!`
- **OTA Port**: 3232 (default)
- **IP**: Check serial monitor or router DHCP table
