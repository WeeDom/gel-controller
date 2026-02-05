# ESP32-CAM OTA Update Setup

## Overview
OTA (Over-The-Air) updates allow you to upload new firmware to the ESP32-CAM over WiFi without needing to connect it via USB.

## Initial Setup (One-time USB upload required)

### 1. Change Partition Scheme
In Arduino IDE:
- Go to **Tools → Partition Scheme**
- Change from "Huge APP (3MB No OTA / 1MB SPIFFS)"
- To: **"Default 4MB with spiffs (1.2MB APP/1.5MB SPIFFS)"**

### 2. Upload Firmware via USB
Upload the modified firmware once via USB cable as normal. This is required to flash the OTA-enabled firmware.

## Using OTA Updates

### Method 1: Arduino IDE
After the initial upload, the ESP32-CAM will appear as a network port:

1. In Arduino IDE, go to **Tools → Port**
2. You should see: `esp32cam-1 at <IP_ADDRESS>` (e.g., `esp32cam-1 at 192.168.1.100`)
3. Select this network port
4. Click Upload - the firmware will be uploaded over WiFi
5. **Password**: `B0ll0cks!` (will be prompted if needed)

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
1. Change partition scheme back to "Huge APP"
2. Upload via USB (OTA won't work with new partition)
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
