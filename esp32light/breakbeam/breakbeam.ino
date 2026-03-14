/**
 * Guard-e-loo Break-Beam Sensor Firmware
 * Board: ESP32 NodeMCU WROOM-32 Dev (30-pin)
 *
 * Monitors a digital break-beam sensor.  When the beam is interrupted
 * (HIGH → LOW) it POSTs a signed webhook to the GEL controller on the LAN.
 * Authenticates with the same HMAC-SHA256 scheme used by the ESP32-CAM firmware.
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <WebServer.h>
#include <ArduinoOTA.h>
#include <ESPmDNS.h>
#include <mbedtls/md.h>
#include <time.h>

// ===========================
// Configuration — edit these
// ===========================
const char* WIFI_SSID        = "guard-e-loo-lan";
const char* WIFI_PASSWORD    = "B0ll0cks!";

// mDNS hostname of the controller on the LAN (no .local suffix needed here)
// The Pi advertises itself as gel-controller.local
const char* CONTROLLER_HOSTNAME = "gel-controller";  // resolves via mDNS to the Pi
const int   CONTROLLER_PORT     = 8765;

// Must match GEL_CAMERA_AUTH_SECRET in the controller .env
const char* SHARED_SECRET   = "niapcinimod";

// Identifies this sensor in the controller logs / webhook payload
const char* SENSOR_ID       = "breakbeam-door1";

// Room this sensor guards (must match room_id configured in gel.py)
const char* ROOM_ID         = "101";

// GPIO pin wired to the receiver side of the break-beam module
#define SENSOR_PIN    25

// Minimum ms between successive edge detections (debounce)
#define DEBOUNCE_MS   80

// ===========================
// Auth header struct (must be declared before use)
// ===========================
struct AuthHeaders {
  char timestamp[16];
  char nonce[25];
  char signature[65];
};

// ===========================
// HMAC-SHA256 helper
// ===========================
static bool hmac_sha256_hex(const char* secret, const char* message,
                             char* out_hex, size_t out_hex_len) {
  unsigned char digest[32];
  const mbedtls_md_info_t* md_info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
  if (!md_info) return false;
  int rc = mbedtls_md_hmac(md_info,
                            (const unsigned char*)secret, strlen(secret),
                            (const unsigned char*)message, strlen(message),
                            digest);
  if (rc != 0) return false;
  for (int i = 0; i < 32; i++) {
    snprintf(out_hex + i * 2, 3, "%02x", digest[i]);
  }
  return true;
}

// ===========================
// Auth header builder
// Signing scheme mirrors camera_auth.py:
//   METHOD\nPATH\nQUERY\nTIMESTAMP\nNONCE
// ===========================
static uint32_t g_nonce_ctr = 0;

static void build_auth_headers(const char* method, const char* path, AuthHeaders& h) {
  // Wall-clock Unix time — must match the controller's time.time() check
  snprintf(h.timestamp, sizeof(h.timestamp), "%ld", (long)time(NULL));

  snprintf(h.nonce, sizeof(h.nonce), "%08lx%08lx",
           (unsigned long)(ESP.getEfuseMac() & 0xFFFFFFFF),
           (unsigned long)g_nonce_ctr++);

  char signing_input[256];
  // query string is empty for this endpoint
  snprintf(signing_input, sizeof(signing_input), "%s\n%s\n\n%s\n%s",
           method, path, h.timestamp, h.nonce);

  hmac_sha256_hex(SHARED_SECRET, signing_input, h.signature, sizeof(h.signature));
}

// ===========================
// Device identity
// ===========================
static char device_mac[18] = "";

// ===========================
// mDNS resolution
// ===========================
static IPAddress g_controller_ip;
static bool      g_controller_resolved = false;

static void resolve_controller() {
  IPAddress ip = MDNS.queryHost(CONTROLLER_HOSTNAME, 2000);
  if (ip != IPAddress(0, 0, 0, 0)) {
    g_controller_ip       = ip;
    g_controller_resolved = true;
    Serial.printf("mDNS: %s.local \u2192 %s\n",
                  CONTROLLER_HOSTNAME, ip.toString().c_str());
  } else {
    g_controller_resolved = false;
    Serial.printf("mDNS: could not resolve %s.local\n", CONTROLLER_HOSTNAME);
  }
}

// ===========================
// WiFi helpers
// ===========================
static void wifi_connect() {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.printf("Connecting to %s", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  for (int tries = 0; tries < 40 && WiFi.status() != WL_CONNECTED; tries++) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("WiFi connected: %s\n", WiFi.localIP().toString().c_str());
    String mac = WiFi.macAddress();
    strlcpy(device_mac, mac.c_str(), sizeof(device_mac));
    // Sync wall-clock time so HMAC timestamps match the controller's Unix time
    configTime(0, 0, "pool.ntp.org", "time.nist.gov");
    Serial.print("NTP sync");
    time_t now = 0;
    for (int i = 0; i < 20 && now < 1000000000L; i++) {
      delay(500);
      Serial.print(".");
      time(&now);
    }
    Serial.printf(" done: %ld\n", (long)now);
    MDNS.begin(SENSOR_ID);   // advertise ourselves too
    resolve_controller();
  } else {
    Serial.println("WiFi connect failed — will retry on next event");
  }
}

// ===========================
// Webhook POST
// ===========================
static void send_webhook(bool beam_broken) {
  wifi_connect();
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("No WiFi — dropping webhook");
    return;
  }

  if (!g_controller_resolved) {
    resolve_controller();
    if (!g_controller_resolved) {
      Serial.println("Controller not reachable via mDNS — dropping webhook");
      return;
    }
  }

  const char* path = "/api/v1/sensor/breakbeam";
  AuthHeaders h;
  build_auth_headers("POST", path, h);

  char url[128];
  snprintf(url, sizeof(url), "http://%s:%d%s",
           g_controller_ip.toString().c_str(), CONTROLLER_PORT, path);

  char body[256];
  snprintf(body, sizeof(body),
           "{\"sensor_id\":\"%s\",\"room_id\":\"%s\",\"beam_broken\":%s}",
           SENSOR_ID, ROOM_ID, beam_broken ? "true" : "false");

  HTTPClient http;
  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Controller-Id", SENSOR_ID);
  http.addHeader("X-Timestamp", h.timestamp);
  http.addHeader("X-Nonce", h.nonce);
  http.addHeader("X-Signature", h.signature);
  http.setTimeout(4000);

  int code = http.POST(body);
  Serial.printf("Webhook → %s  HTTP %d\n", url, code);
  http.end();
}

// ===========================
// Setup & Loop
// ===========================
static int   last_state     = HIGH;
static unsigned long last_change_ms = 0;
static bool  g_stopped      = false;

WebServer server(80);

static void set_device_headers() {
  server.sendHeader("X-Device-Type", "gel-breakbeam");
  server.sendHeader("X-Device-ID",   device_mac);
  server.sendHeader("X-Device-Name", SENSOR_ID);
  server.sendHeader("X-Room-ID",     ROOM_ID);
}

// Handles HEAD / and GET / — allows the controller discovery scanner to
// identify this device via X-Device-Type without having to know /health.
static void handle_root() {
  set_device_headers();
  server.send(200, "application/json", "");
}

static void handle_health() {
  char buf[256];
  snprintf(buf, sizeof(buf),
    "{\"ok\":true,\"sensor_id\":\"%s\",\"room_id\":\"%s\","
    "\"stopped\":%s,\"uptime_s\":%llu,\"ip\":\"%s\"}",
    SENSOR_ID, ROOM_ID,
    g_stopped ? "true" : "false",
    (unsigned long long)(esp_timer_get_time() / 1000000ULL),
    WiFi.localIP().toString().c_str());
  set_device_headers();
  server.send(200, "application/json", buf);
}

static void handle_stop() {
  g_stopped = true;
  Serial.println("Sensor STOPPED via /stop");
  set_device_headers();
  server.send(200, "application/json",
    "{\"ok\":true,\"message\":\"sensor stopped\"}");
}

static void handle_start() {
  g_stopped = false;
  Serial.println("Sensor STARTED via /start");
  set_device_headers();
  server.send(200, "application/json",
    "{\"ok\":true,\"message\":\"sensor started\"}");
}

static void handle_not_found() {
  server.send(404, "application/json", "{\"error\":\"not found\"}");
}

void setup() {
  Serial.begin(115200);
  pinMode(SENSOR_PIN, INPUT_PULLUP);

  wifi_connect();

  ArduinoOTA.setHostname(SENSOR_ID);
  ArduinoOTA.begin();

  // Inbound command server
  server.on("/",       HTTP_GET,  handle_root);
  server.on("/",       HTTP_HEAD, handle_root);
  server.on("/health", HTTP_GET, handle_health);
  server.on("/stop",   HTTP_POST, handle_stop);
  server.on("/start",  HTTP_POST, handle_start);
  server.onNotFound(handle_not_found);
  server.begin();
  Serial.println("Command server listening on port 80");

  Serial.printf("Break-beam ready on GPIO%d  sensor=%s  room=%s\n",
                SENSOR_PIN, SENSOR_ID, ROOM_ID);
}

void loop() {
  ArduinoOTA.handle();
  server.handleClient();

  if (g_stopped) {
    delay(10);
    return;
  }

  int state = digitalRead(SENSOR_PIN);
  unsigned long now = millis();

  // Debug: print current pin level every second so we can see if it ever changes
  static unsigned long last_debug_ms = 0;
  if (now - last_debug_ms >= 1000) {
    last_debug_ms = now;
    Serial.printf("[DEBUG] GPIO%d = %s  (last_state=%s)\n",
                  SENSOR_PIN,
                  state == HIGH ? "HIGH" : "LOW",
                  last_state == HIGH ? "HIGH" : "LOW");
  }

  if (state != last_state && (now - last_change_ms) >= DEBOUNCE_MS) {
    last_change_ms = now;
    last_state = state;

    if (state == LOW) {
      Serial.println("Beam BROKEN — person crossing threshold");
      send_webhook(true);
    } else {
      Serial.println("Beam CLEAR");
      send_webhook(false);
    }
  }

  // Reconnect watchdog (non-blocking — next event will also retry)
  if (WiFi.status() != WL_CONNECTED) {
    wifi_connect();
  }

  delay(10);
}

