# Guard-e-loo ESP32-CAM Firmware — Dependency Manifest

## 📦 Build Environment
| Component | Version | Notes |
|------------|----------|-------|
| **Arduino IDE** | 1.8.19 | Stable legacy IDE, preferred for ESP32-CAM builds |
| **ESP32 Arduino Core** | 2.0.14 | Tested and verified (Espressif official) |
| **Board Definition** | AI Thinker ESP32-CAM | Configured under *Tools → Board → ESP32 Arduino → AI Thinker ESP32-CAM* |
| **Upload Speed** | 115200 baud | Reliable for all boards tested |
| **Flash Mode** | QIO | Default for AI-Thinker module |
| **Flash Frequency** | 40 MHz | Default and stable |
| **Partition Scheme** | Huge APP (3 MB No OTA / 1 MB SPIFFS) | Required for camera firmware size |

---

## 📚 Library Dependencies
All libraries are fixed to the versions listed below for deterministic builds.
Do **not** update or replace unless specifically tested and tagged.

| Library | Version | Source | Included With |
|----------|----------|---------|----------------|
| **esp32-camera** | 1.0.0 | [github.com/espressif/esp32-camera](https://github.com/espressif/esp32-camera) | ESP32 core |
| **WiFi** | 1.0.0 | Built-in | ESP32 core |
| **FS** | 1.0.0 | Built-in | ESP32 core |
| **SPIFFS** | 1.0.0 | Built-in | ESP32 core |
| **Arduino** | 1.8.x | [arduino.cc](https://www.arduino.cc/en/software) | Global |

---

## 🧱 Firmware Components
| File | Purpose |
|------|----------|
| `CameraWebServer.ino` | Main firmware sketch |
| `board_config.h` | Camera model selection (AI_THINKER) |
| `app_httpd.cpp` | HTTP server implementation |
| `camera_index.h` | Web UI controls |
| `camera_pins.h` | Pin definitions for AI Thinker board |

---

## 🧩 Environment Notes
- Built and tested on **Ubuntu 22.04 LTS**
- Python `esptool` v5.1.0 used for flash erase and manual upload verification
- Serial device: `/dev/ttyUSB0`
- Board verified with **AI-Thinker ESP32-CAM (ESP32-D0WD-V3)**

---

## 🚫 Update Policy
> Guard-e-loo firmware is tested against the above environment and library versions.
> Upgrading any dependency (ESP32 core or libraries) may alter APIs, timing, or memory layout, potentially breaking camera initialization or Wi-Fi setup.
> Always rebuild and verify on a staging board before field deployment.

---

## 🏷️ Version Control Recommendations
- Tag all confirmed-working firmware builds, e.g.:
  ```bash
  git tag -a v0.3.0 -m "Stable ESP32-CAM build (ESP32 core 2.0.14)"
  git push origin v0.3.0
