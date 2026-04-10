#include "esp_camera.h"
#include <WiFi.h>
#include <time.h>
#include <ArduinoOTA.h>
#include "esp_wifi.h"
#include "esp_pm.h"

#include "board_config.h"

const char *WIFI_SSID = "guard-e-loo-lan";
const char *WIFI_PASSWORD = "B0ll0cks!";

const char *DEVICE_NAME = "doorcam1";
const char *ROOM_ID = "unknown";

const char *NTP_SERVER = "pool.ntp.org";
const long GMT_OFFSET_SEC = 0;
const int DAYLIGHT_OFFSET_SEC = 3600;

const unsigned long HEARTBEAT_INTERVAL_MS = 30000UL;
const framesize_t DOORCAM_FRAME_SIZE = FRAMESIZE_VGA;
const int DOORCAM_JPEG_QUALITY = 12;
const int DOORCAM_BURST_COUNT = 3;
const int DOORCAM_BURST_GAP_MS = 80;

void startDoorCameraServer();
void setupLedFlash();
const char *doorcam_device_name();
const char *doorcam_room_id();
const char *doorcam_device_mac();
int doorcam_default_burst_count();
int doorcam_burst_gap_ms();

static unsigned long lastHeartbeatMs = 0;

const char *wifiStatusToString(wl_status_t status) {
  switch (status) {
    case WL_IDLE_STATUS: return "IDLE";
    case WL_NO_SSID_AVAIL: return "NO_SSID_AVAIL";
    case WL_SCAN_COMPLETED: return "SCAN_COMPLETED";
    case WL_CONNECTED: return "CONNECTED";
    case WL_CONNECT_FAILED: return "CONNECT_FAILED";
    case WL_CONNECTION_LOST: return "CONNECTION_LOST";
    case WL_DISCONNECTED: return "DISCONNECTED";
    default: return "UNKNOWN";
  }
}

const char *doorcam_device_name() {
  return DEVICE_NAME;
}

const char *doorcam_room_id() {
  return ROOM_ID;
}

int doorcam_default_burst_count() {
  return DOORCAM_BURST_COUNT;
}

int doorcam_burst_gap_ms() {
  return DOORCAM_BURST_GAP_MS;
}

static void printNetworkIdentity() {
  Serial.println("=== DOOR CAMERA ===");
  Serial.print("Name: ");
  Serial.println(DEVICE_NAME);
  Serial.print("Room: ");
  Serial.println(ROOM_ID);
  Serial.print("STA MAC: ");
  Serial.println(WiFi.macAddress());
  Serial.print("Device MAC: ");
  Serial.println(doorcam_device_mac());
  Serial.print("IP: ");
  Serial.println(WiFi.localIP());
  Serial.print("Gateway: ");
  Serial.println(WiFi.gatewayIP());
  Serial.print("Subnet: ");
  Serial.println(WiFi.subnetMask());
  Serial.print("DNS: ");
  Serial.println(WiFi.dnsIP());
  Serial.print("BSSID: ");
  Serial.println(WiFi.BSSIDstr());
  Serial.print("Channel: ");
  Serial.println(WiFi.channel());
  Serial.print("PSRAM: ");
  Serial.println(psramFound() ? "yes" : "no");
  Serial.println("===================");
}

static void printHeartbeat() {
  Serial.print("[heartbeat] uptime_ms=");
  Serial.print(millis());
  Serial.print(" wifi_status=");
  Serial.print(wifiStatusToString(WiFi.status()));
  Serial.print(" ip=");
  Serial.print(WiFi.localIP());
  Serial.print(" rssi=");
  Serial.print(WiFi.RSSI());
  Serial.print(" name=");
  Serial.print(DEVICE_NAME);
  Serial.print(" room=");
  Serial.println(ROOM_ID);
}

static bool connectWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.disconnect(true, true);
  delay(500);
  WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  // Disable WiFi modem sleep at the IDF level (WiFi.setSleep alone is insufficient on Arduino-ESP32 3.x)
  esp_wifi_set_ps(WIFI_PS_NONE);

  // Lock CPU at 240 MHz and disable automatic light sleep
  esp_pm_config_esp32_t pm_config = {
      .max_freq_mhz       = 240,
      .min_freq_mhz       = 240,
      .light_sleep_enable = false,
  };
  esp_pm_configure(&pm_config);

  Serial.print("WiFi connecting to ");
  Serial.println(WIFI_SSID);

  unsigned long startMs = millis();
  while (WiFi.status() != WL_CONNECTED) {
    Serial.print(".");
    delay(500);
    if (millis() - startMs > 20000UL) {
      Serial.println();
      Serial.println("WiFi connection timed out");
      return false;
    }
  }

  Serial.println();
  Serial.println("WiFi connected");
  return true;
}

static bool initClock() {
  configTime(GMT_OFFSET_SEC, DAYLIGHT_OFFSET_SEC, NTP_SERVER);
  struct tm timeinfo;
  if (!getLocalTime(&timeinfo, 5000)) {
    Serial.println("NTP sync failed");
    return false;
  }

  Serial.print("Current time: ");
  Serial.println(&timeinfo, "%A, %B %d %Y %H:%M:%S");
  return true;
}

static void initOta() {
  ArduinoOTA.setHostname(doorcam_device_name());

  ArduinoOTA.onStart([]() {
    String type;
    if (ArduinoOTA.getCommand() == U_FLASH) {
      type = "sketch";
    } else {
      type = "filesystem";
    }
    Serial.println("OTA start: " + type);
  });

  ArduinoOTA.onEnd([]() {
    Serial.println("\nOTA end");
  });

  ArduinoOTA.onProgress([](unsigned int progress, unsigned int total) {
    Serial.printf("OTA progress: %u%%\r", (progress / (total / 100)));
  });

  ArduinoOTA.onError([](ota_error_t error) {
    Serial.printf("OTA error[%u]: ", error);
    if (error == OTA_AUTH_ERROR) {
      Serial.println("Auth Failed");
    } else if (error == OTA_BEGIN_ERROR) {
      Serial.println("Begin Failed");
    } else if (error == OTA_CONNECT_ERROR) {
      Serial.println("Connect Failed");
    } else if (error == OTA_RECEIVE_ERROR) {
      Serial.println("Receive Failed");
    } else if (error == OTA_END_ERROR) {
      Serial.println("End Failed");
    }
  });

  ArduinoOTA.begin();
  Serial.println("OTA ready");
}

static bool initCamera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.frame_size = DOORCAM_FRAME_SIZE;
  config.pixel_format = PIXFORMAT_JPEG;
  config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.jpeg_quality = DOORCAM_JPEG_QUALITY;
  config.fb_count = 1;

  if (!psramFound()) {
    config.fb_location = CAMERA_FB_IN_DRAM;
  }

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed with error 0x%x\n", err);
    return false;
  }

  sensor_t *s = esp_camera_sensor_get();
  if (s != NULL) {
    if (s->id.PID == OV3660_PID) {
      s->set_vflip(s, 1);
      s->set_brightness(s, 1);
      s->set_saturation(s, -2);
    }

#if defined(CAMERA_MODEL_M5STACK_WIDE) || defined(CAMERA_MODEL_M5STACK_ESP32CAM)
    s->set_vflip(s, 1);
    s->set_hmirror(s, 1);
#endif

#if defined(CAMERA_MODEL_ESP32S3_EYE)
    s->set_vflip(s, 1);
#endif

    s->set_framesize(s, DOORCAM_FRAME_SIZE);
  }

#if defined(LED_GPIO_NUM)
  setupLedFlash();
#endif

  return true;
}

void setup() {
  Serial.begin(115200);
  Serial.setDebugOutput(true);
  Serial.println();

  if (!initCamera()) {
    return;
  }

  if (!connectWifi()) {
    return;
  }

  initClock();
  initOta();
  startDoorCameraServer();
  printNetworkIdentity();
  Serial.print("Door camera ready at http://");
  Serial.println(WiFi.localIP());

  printHeartbeat();
  lastHeartbeatMs = millis();
}

void loop() {
  ArduinoOTA.handle();

  if (millis() - lastHeartbeatMs >= HEARTBEAT_INTERVAL_MS) {
    printHeartbeat();
    lastHeartbeatMs = millis();
  }
  vTaskDelay(pdMS_TO_TICKS(10));  // yield to scheduler without triggering light sleep
}
