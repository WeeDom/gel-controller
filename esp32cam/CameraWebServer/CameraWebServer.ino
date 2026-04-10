#include "esp_camera.h"
#include <WiFi.h>
#include <time.h>
#include <ArduinoOTA.h>
#include "esp_wifi.h"
#include "esp_pm.h"

// ===========================
// Select camera model in board_config.h
// ===========================
#include "board_config.h"

// ===========================
// Enter your WiFi credentials
// ===========================
const char *ssid = "guard-e-loo-lan";
const char *password = "B0ll0cks!";

// ===========================
// Time configuration
// ===========================
const char* ntpServer = "pool.ntp.org";
const long gmtOffset_sec = 0;        // GMT+0 for UK
const int daylightOffset_sec = 3600; // 1 hour for BST
unsigned long lastReconnectAttemptMs = 0;
bool wifiStarted = false;

void startCameraServer();
void setupLedFlash();
static esp_err_t initCameraWithFallback(camera_config_t *config);

void printWifiDetails() {
  Serial.print("SSID: ");
  Serial.println(WiFi.SSID());
  Serial.print("BSSID: ");
  Serial.println(WiFi.BSSIDstr());
  Serial.print("Channel: ");
  Serial.println(WiFi.channel());
  Serial.print("RSSI: ");
  Serial.print(WiFi.RSSI());
  Serial.println(" dBm");
  Serial.print("Local IP: ");
  Serial.println(WiFi.localIP());
  Serial.print("Gateway: ");
  Serial.println(WiFi.gatewayIP());
  Serial.print("Subnet: ");
  Serial.println(WiFi.subnetMask());
  Serial.print("DNS: ");
  Serial.println(WiFi.dnsIP());
}

const char* wifiStatusToString(wl_status_t status) {
  switch(status) {
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

void setup() {
  Serial.begin(115200);
  Serial.setDebugOutput(true);
  Serial.println();

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
  config.frame_size = FRAMESIZE_UXGA;
  config.pixel_format = PIXFORMAT_JPEG;  // for streaming
  //config.pixel_format = PIXFORMAT_RGB565; // for face detection/recognition
  config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.jpeg_quality = 12;
  config.fb_count = 1;

  // if PSRAM IC present, init with UXGA resolution and higher JPEG quality
  //                      for larger pre-allocated frame buffer.
  if (config.pixel_format == PIXFORMAT_JPEG) {
    if (psramFound()) {
      config.jpeg_quality = 10;
      config.fb_count = 2;
      config.grab_mode = CAMERA_GRAB_LATEST;
    } else {
      // Limit the frame size when PSRAM is not available
      config.frame_size = FRAMESIZE_SVGA;
      config.fb_location = CAMERA_FB_IN_DRAM;
    }
  } else {
    // Best option for face detection/recognition
    config.frame_size = FRAMESIZE_240X240;
#if CONFIG_IDF_TARGET_ESP32S3
    config.fb_count = 2;
#endif
  }

#if defined(CAMERA_MODEL_ESP_EYE)
  pinMode(13, INPUT_PULLUP);
  pinMode(14, INPUT_PULLUP);
#endif

  // camera init
  esp_err_t err = initCameraWithFallback(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed with error 0x%x\n", err);
    return;
  }

  sensor_t *s = esp_camera_sensor_get();
  // initial sensors are flipped vertically and colors are a bit saturated
  if (s->id.PID == OV3660_PID) {
    s->set_vflip(s, 1);        // flip it back
    s->set_brightness(s, 1);   // up the brightness just a bit
    s->set_saturation(s, -2);  // lower the saturation
  }
  // drop down frame size for higher initial frame rate
  if (config.pixel_format == PIXFORMAT_JPEG) {
    s->set_framesize(s, FRAMESIZE_QVGA);
  }

#if defined(CAMERA_MODEL_M5STACK_WIDE) || defined(CAMERA_MODEL_M5STACK_ESP32CAM)
  s->set_vflip(s, 1);
  s->set_hmirror(s, 1);
#endif

#if defined(CAMERA_MODEL_ESP32S3_EYE)
  s->set_vflip(s, 1);
#endif

// Setup LED FLash if LED pin is defined in camera_pins.h
#if defined(LED_GPIO_NUM)
  setupLedFlash();
#endif

  // Scan for available networks first
  Serial.println("Scanning for WiFi networks...");
  int n = WiFi.scanNetworks();
  Serial.print("Found ");
  Serial.print(n);
  Serial.println(" networks:");
  for (int i = 0; i < n; i++) {
    Serial.print(i + 1);
    Serial.print(": ");
    Serial.print(WiFi.SSID(i));
    Serial.print(" (");
    Serial.print(WiFi.RSSI(i));
    Serial.print(" dBm) Ch:");
    Serial.print(WiFi.channel(i));
    Serial.println(WiFi.encryptionType(i) == WIFI_AUTH_OPEN ? " [OPEN]" : " [SECURED]");
  }
  Serial.println("");

  // Avoid pinning a channel so reconnects still work if AP channel changes.
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(true);
  WiFi.persistent(false);
  wifiStarted = true;

  // Disable WiFi modem sleep at the IDF level (WiFi.setSleep alone is insufficient on Arduino-ESP32 3.x)
  esp_wifi_set_ps(WIFI_PS_NONE);

  // Lock CPU at 240 MHz and disable automatic light sleep
  esp_pm_config_esp32_t pm_config = {
      .max_freq_mhz       = 240,
      .min_freq_mhz       = 240,
      .light_sleep_enable = false,
  };
  esp_pm_configure(&pm_config);

  Serial.print("WiFi connecting to: ");
  Serial.print(ssid);
  Serial.println(" (auto channel)");
  while (WiFi.status() != WL_CONNECTED) {
    Serial.print("\nStatus: ");
    Serial.print(wifiStatusToString(WiFi.status()));
    Serial.print(".");
    delay(500);
  }
  Serial.println("");
  Serial.println("WiFi connected");
  printWifiDetails();

  // Initialize and get the time
  Serial.println("Initializing time...");
  configTime(gmtOffset_sec, daylightOffset_sec, ntpServer);

  struct tm timeinfo;
  if(getLocalTime(&timeinfo)){
    Serial.print("Current time: ");
    Serial.println(&timeinfo, "%A, %B %d %Y %H:%M:%S");
  } else {
    Serial.println("Failed to obtain time");
  }

  startCameraServer();

  // Configure OTA updates
  ArduinoOTA.setHostname("esp32cam-1");

  ArduinoOTA.onStart([]() {
    String type;
    if (ArduinoOTA.getCommand() == U_FLASH) {
      type = "sketch";
    } else {  // U_SPIFFS
      type = "filesystem";
    }
    Serial.println("Start updating " + type);
  });

  ArduinoOTA.onEnd([]() {
    Serial.println("\nEnd");
  });

  ArduinoOTA.onProgress([](unsigned int progress, unsigned int total) {
    Serial.printf("Progress: %u%%\r", (progress / (total / 100)));
  });

  ArduinoOTA.onError([](ota_error_t error) {
    Serial.printf("Error[%u]: ", error);
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
  Serial.println("OTA Ready");

  Serial.print("Camera 2 Ready! Use 'http://");
  Serial.print(WiFi.localIP());
  Serial.println("' to connect");
}

void loop() {
  if (wifiStarted && WiFi.status() != WL_CONNECTED) {
    unsigned long now = millis();
    if (now - lastReconnectAttemptMs > 5000) {
      lastReconnectAttemptMs = now;
      Serial.print("WiFi dropped, status=");
      Serial.println(wifiStatusToString(WiFi.status()));
      Serial.println("Attempting reconnect...");
      WiFi.reconnect();
    }
  }

  // Handle OTA updates
  ArduinoOTA.handle();
  vTaskDelay(pdMS_TO_TICKS(10));  // yield to scheduler without triggering light sleep
}

static esp_err_t initCameraWithFallback(camera_config_t *config) {
  esp_err_t err = esp_camera_init(config);
  if (err == ESP_OK) {
    return err;
  }

  if (err != ESP_ERR_NOT_SUPPORTED || config->pixel_format != PIXFORMAT_JPEG) {
    return err;
  }

  Serial.println("Native JPEG is not supported on this sensor. Retrying with RGB565.");
  config->pixel_format = PIXFORMAT_RGB565;
  config->frame_size = psramFound() ? FRAMESIZE_SVGA : FRAMESIZE_QVGA;
  config->fb_count = 1;
  config->grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  config->fb_location = psramFound() ? CAMERA_FB_IN_PSRAM : CAMERA_FB_IN_DRAM;

  return esp_camera_init(config);
}
