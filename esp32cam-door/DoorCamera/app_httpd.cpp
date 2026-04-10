#include "esp_http_server.h"
#include "esp_camera.h"
#include "esp_timer.h"
#include "img_converters.h"
#include "esp32-hal-ledc.h"
#include <WiFi.h>
#include <mbedtls/md.h>
#include <string.h>
#include <time.h>
#include <stdlib.h>

#if defined(ARDUINO_ARCH_ESP32) && defined(CONFIG_ARDUHAL_ESP_LOG)
#include "esp32-hal-log.h"
#endif

extern const char *doorcam_device_name();
extern const char *doorcam_room_id();
extern int doorcam_default_burst_count();
extern int doorcam_burst_gap_ms();

#if defined(LED_GPIO_NUM)
#define CONFIG_LED_MAX_INTENSITY 255
static int led_duty = CONFIG_LED_MAX_INTENSITY;

void enable_led(bool en) {
  ledcWrite(LED_GPIO_NUM, en ? led_duty : 0);
}
#endif

static httpd_handle_t door_httpd = NULL;
static char device_mac[18] = "";

static const bool AUTH_ENABLED = true;
static const char *AUTH_CONTROLLER_ID = "gel-controller-1";
static const char *AUTH_SHARED_SECRET = "niapcinimod";
static const long AUTH_MAX_SKEW_SECONDS = 45;
static const size_t AUTH_NONCE_CACHE_SIZE = 32;

typedef struct {
  char nonce[40];
  long seen_at;
} auth_nonce_entry_t;

static auth_nonce_entry_t auth_nonce_cache[AUTH_NONCE_CACHE_SIZE];
static size_t auth_nonce_cache_pos = 0;

typedef struct {
  uint8_t *data;
  size_t len;
  timeval timestamp;
  uint32_t sequence;
  uint32_t requested_burst;
  bool valid;
} stored_capture_t;

static stored_capture_t g_last_capture = {0};

const char *doorcam_device_mac() {
  return device_mac;
}

static void clear_nonce_cache() {
  memset(auth_nonce_cache, 0, sizeof(auth_nonce_cache));
  auth_nonce_cache_pos = 0;
}

static void set_common_headers(httpd_req_t *req) {
  // Keep discovery-compatible type while exposing door-specific role separately.
  httpd_resp_set_hdr(req, "X-Device-Type", "gel-camera");
  httpd_resp_set_hdr(req, "X-Device-Role", "doorcam");
  httpd_resp_set_hdr(req, "X-Device-ID", device_mac);
  httpd_resp_set_hdr(req, "X-Device-Name", doorcam_device_name());
  httpd_resp_set_hdr(req, "X-Room-ID", doorcam_room_id());
  httpd_resp_set_hdr(req, "Cache-Control", "no-store");
}

static bool get_header_value(httpd_req_t *req, const char *name, char *out, size_t out_len) {
  size_t hdr_len = httpd_req_get_hdr_value_len(req, name);
  if (hdr_len == 0 || hdr_len + 1 > out_len) {
    return false;
  }
  return httpd_req_get_hdr_value_str(req, name, out, out_len) == ESP_OK;
}

static bool get_query_string(httpd_req_t *req, char *out, size_t out_len) {
  size_t query_len = httpd_req_get_url_query_len(req);
  if (query_len == 0) {
    out[0] = '\0';
    return true;
  }
  if (query_len + 1 > out_len) {
    return false;
  }
  return httpd_req_get_url_query_str(req, out, out_len) == ESP_OK;
}

static const char *method_to_string(int method) {
  switch (method) {
    case HTTP_GET: return "GET";
    case HTTP_POST: return "POST";
    case HTTP_PUT: return "PUT";
    case HTTP_DELETE: return "DELETE";
    case HTTP_HEAD: return "HEAD";
    case HTTP_PATCH: return "PATCH";
    default: return "OTHER";
  }
}

static bool timing_safe_hex_equals(const char *a, const char *b) {
  size_t al = strlen(a);
  size_t bl = strlen(b);
  if (al != bl) {
    return false;
  }
  unsigned char diff = 0;
  for (size_t i = 0; i < al; ++i) {
    diff |= (unsigned char)(a[i] ^ b[i]);
  }
  return diff == 0;
}

static bool hmac_sha256_hex(const char *secret, const char *message, char *out_hex, size_t out_hex_len) {
  if (out_hex_len < 65) {
    return false;
  }

  unsigned char digest[32];
  const mbedtls_md_info_t *md_info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
  if (!md_info) {
    return false;
  }

  int rc = mbedtls_md_hmac(
    md_info,
    (const unsigned char *)secret,
    strlen(secret),
    (const unsigned char *)message,
    strlen(message),
    digest
  );
  if (rc != 0) {
    return false;
  }

  for (size_t i = 0; i < sizeof(digest); ++i) {
    snprintf(out_hex + (i * 2), 3, "%02x", digest[i]);
  }
  out_hex[64] = '\0';
  return true;
}

static bool nonce_seen_recently(const char *nonce, long now_ts) {
  for (size_t i = 0; i < AUTH_NONCE_CACHE_SIZE; ++i) {
    if (auth_nonce_cache[i].nonce[0] == '\0') {
      continue;
    }
    if (strcmp(auth_nonce_cache[i].nonce, nonce) == 0 &&
        labs(now_ts - auth_nonce_cache[i].seen_at) <= AUTH_MAX_SKEW_SECONDS) {
      return true;
    }
  }
  return false;
}

static void remember_nonce(const char *nonce, long now_ts) {
  strlcpy(auth_nonce_cache[auth_nonce_cache_pos].nonce, nonce, sizeof(auth_nonce_cache[auth_nonce_cache_pos].nonce));
  auth_nonce_cache[auth_nonce_cache_pos].seen_at = now_ts;
  auth_nonce_cache_pos = (auth_nonce_cache_pos + 1) % AUTH_NONCE_CACHE_SIZE;
}

static esp_err_t send_json_error(httpd_req_t *req, const char *status, const char *body) {
  httpd_resp_set_status(req, status);
  httpd_resp_set_type(req, "application/json");
  set_common_headers(req);
  return httpd_resp_sendstr(req, body);
}

static bool authorize_request(httpd_req_t *req) {
  if (!AUTH_ENABLED) {
    return true;
  }

  char controller_id[48];
  char timestamp_str[24];
  char nonce[40];
  char signature[96];
  char query[192];

  if (!get_header_value(req, "X-Controller-Id", controller_id, sizeof(controller_id)) ||
      !get_header_value(req, "X-Timestamp", timestamp_str, sizeof(timestamp_str)) ||
      !get_header_value(req, "X-Nonce", nonce, sizeof(nonce)) ||
      !get_header_value(req, "X-Signature", signature, sizeof(signature))) {
    send_json_error(req, "401 Unauthorized", "{\"ok\":false,\"error\":\"missing_auth_headers\"}");
    return false;
  }

  if (strcmp(controller_id, AUTH_CONTROLLER_ID) != 0) {
    send_json_error(req, "403 Forbidden", "{\"ok\":false,\"error\":\"controller_mismatch\"}");
    return false;
  }

  char *endptr = NULL;
  long request_ts = strtol(timestamp_str, &endptr, 10);
  if (endptr == timestamp_str || *endptr != '\0') {
    send_json_error(req, "400 Bad Request", "{\"ok\":false,\"error\":\"invalid_timestamp\"}");
    return false;
  }

  long now_ts = (long)time(NULL);
  if (now_ts <= 0 || labs(now_ts - request_ts) > AUTH_MAX_SKEW_SECONDS) {
    send_json_error(req, "408 Request Timeout", "{\"ok\":false,\"error\":\"stale_timestamp\"}");
    return false;
  }

  if (!get_query_string(req, query, sizeof(query))) {
    send_json_error(req, "400 Bad Request", "{\"ok\":false,\"error\":\"query_too_long\"}");
    return false;
  }

  if (nonce_seen_recently(nonce, now_ts)) {
    send_json_error(req, "409 Conflict", "{\"ok\":false,\"error\":\"nonce_replay\"}");
    return false;
  }

  char signing_input[320];
  snprintf(
    signing_input,
    sizeof(signing_input),
    "%s\n%s\n%s\n%s\n%s",
    method_to_string(req->method),
    req->uri,
    query,
    timestamp_str,
    nonce
  );

  char expected_sig[65];
  if (!hmac_sha256_hex(AUTH_SHARED_SECRET, signing_input, expected_sig, sizeof(expected_sig))) {
    send_json_error(req, "500 Internal Server Error", "{\"ok\":false,\"error\":\"signature_generation_failed\"}");
    return false;
  }

  if (!timing_safe_hex_equals(signature, expected_sig)) {
    send_json_error(req, "401 Unauthorized", "{\"ok\":false,\"error\":\"invalid_signature\"}");
    return false;
  }

  remember_nonce(nonce, now_ts);
  return true;
}

static int query_int_or_default(httpd_req_t *req, const char *key, int def) {
  char query[192];
  char value[16];
  if (!get_query_string(req, query, sizeof(query))) {
    return def;
  }
  if (httpd_query_key_value(query, key, value, sizeof(value)) != ESP_OK) {
    return def;
  }
  return atoi(value);
}

static void replace_last_capture(uint8_t *data, size_t len, const timeval &ts, uint32_t sequence, uint32_t requested_burst) {
  if (g_last_capture.data) {
    free(g_last_capture.data);
  }
  g_last_capture.data = data;
  g_last_capture.len = len;
  g_last_capture.timestamp = ts;
  g_last_capture.sequence = sequence;
  g_last_capture.requested_burst = requested_burst;
  g_last_capture.valid = true;
}

static esp_err_t store_frame(camera_fb_t *fb, uint32_t sequence, uint32_t requested_burst) {
  uint8_t *jpeg_buf = NULL;
  size_t jpeg_len = 0;

  if (fb->format == PIXFORMAT_JPEG) {
    jpeg_len = fb->len;
    jpeg_buf = (uint8_t *)malloc(jpeg_len);
    if (!jpeg_buf) {
      return ESP_ERR_NO_MEM;
    }
    memcpy(jpeg_buf, fb->buf, jpeg_len);
  } else {
    bool ok = frame2jpg(fb, 80, &jpeg_buf, &jpeg_len);
    if (!ok || !jpeg_buf) {
      return ESP_FAIL;
    }
  }

  replace_last_capture(jpeg_buf, jpeg_len, fb->timestamp, sequence, requested_burst);
  return ESP_OK;
}

static esp_err_t capture_burst(uint32_t requested_burst, uint32_t *captured_frames, uint32_t *sequence_out) {
  uint32_t burst = requested_burst;
  if (burst < 1) {
    burst = 1;
  }
  if (burst > 5) {
    burst = 5;
  }

  static uint32_t sequence = 0;
  sequence++;

  uint32_t success_count = 0;
  for (uint32_t i = 0; i < burst; ++i) {
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) {
      log_e("Burst capture failed at frame %u", (unsigned)(i + 1));
      break;
    }

    if (store_frame(fb, sequence, burst) == ESP_OK) {
      success_count++;
    }
    esp_camera_fb_return(fb);

    if (i + 1 < burst) {
      delay(doorcam_burst_gap_ms());
    }
  }

  *captured_frames = success_count;
  *sequence_out = sequence;
  return success_count > 0 ? ESP_OK : ESP_FAIL;
}

static esp_err_t index_head_handler(httpd_req_t *req) {
  Serial.println("Received head request for /");
  set_common_headers(req);
  httpd_resp_set_type(req, "application/json");
  return httpd_resp_send(req, NULL, 0);
}

static esp_err_t health_handler(httpd_req_t *req) {
  char resp[320];
  int len = snprintf(
    resp,
    sizeof(resp),
    "{\"ok\":true,\"device_type\":\"gel-camera\",\"device_role\":\"doorcam\",\"name\":\"%s\",\"room_id\":\"%s\",\"ip\":\"%s\",\"rssi\":%d,\"capture_ready\":%s,\"last_sequence\":%u,\"ota_enabled\":true}",
    doorcam_device_name(),
    doorcam_room_id(),
    WiFi.localIP().toString().c_str(),
    WiFi.RSSI(),
    g_last_capture.valid ? "true" : "false",
    g_last_capture.sequence
  );
  httpd_resp_set_type(req, "application/json");
  set_common_headers(req);
  return httpd_resp_send(req, resp, len);
}

static esp_err_t trigger_handler(httpd_req_t *req) {
  if (!authorize_request(req)) {
    return ESP_FAIL;
  }

  uint32_t requested_burst = (uint32_t)query_int_or_default(req, "burst", doorcam_default_burst_count());
  uint32_t captured_frames = 0;
  uint32_t sequence = 0;

  int64_t started_at = esp_timer_get_time();
  esp_err_t capture_err = capture_burst(requested_burst, &captured_frames, &sequence);
  int64_t ended_at = esp_timer_get_time();

  if (capture_err != ESP_OK) {
    return send_json_error(req, "500 Internal Server Error", "{\"ok\":false,\"error\":\"capture_failed\"}");
  }

  char ts[32];
  snprintf(ts, sizeof(ts), "%lld.%06ld", (long long)g_last_capture.timestamp.tv_sec, (long)g_last_capture.timestamp.tv_usec);

  char resp[384];
  int len = snprintf(
    resp,
    sizeof(resp),
    "{\"ok\":true,\"sequence\":%u,\"captured_frames\":%u,\"requested_burst\":%u,\"capture_ms\":%u,\"latest_path\":\"/latest.jpg\",\"timestamp\":\"%s\"}",
    sequence,
    captured_frames,
    requested_burst,
    (unsigned)((ended_at - started_at) / 1000),
    ts
  );
  httpd_resp_set_type(req, "application/json");
  set_common_headers(req);
  return httpd_resp_send(req, resp, len);
}

static esp_err_t latest_handler(httpd_req_t *req) {
  if (!authorize_request(req)) {
    return ESP_FAIL;
  }

  if (!g_last_capture.valid || !g_last_capture.data || g_last_capture.len == 0) {
    return send_json_error(req, "404 Not Found", "{\"ok\":false,\"error\":\"no_capture_available\"}");
  }

  char ts[32];
  snprintf(ts, sizeof(ts), "%lld.%06ld", (long long)g_last_capture.timestamp.tv_sec, (long)g_last_capture.timestamp.tv_usec);
  char seq[16];
  snprintf(seq, sizeof(seq), "%u", g_last_capture.sequence);

  httpd_resp_set_type(req, "image/jpeg");
  set_common_headers(req);
  httpd_resp_set_hdr(req, "X-Timestamp", ts);
  httpd_resp_set_hdr(req, "X-Capture-Sequence", seq);
  return httpd_resp_send(req, (const char *)g_last_capture.data, g_last_capture.len);
}

void startDoorCameraServer() {
  String mac = WiFi.macAddress();
  strlcpy(device_mac, mac.c_str(), sizeof(device_mac));
  clear_nonce_cache();

  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.max_uri_handlers = 6;

  httpd_uri_t index_head_uri = {
    .uri = "/",
    .method = HTTP_HEAD,
    .handler = index_head_handler,
    .user_ctx = NULL
  };

  httpd_uri_t health_uri = {
    .uri = "/health",
    .method = HTTP_GET,
    .handler = health_handler,
    .user_ctx = NULL
  };

  httpd_uri_t trigger_uri = {
    .uri = "/trigger",
    .method = HTTP_POST,
    .handler = trigger_handler,
    .user_ctx = NULL
  };

  httpd_uri_t latest_uri = {
    .uri = "/latest.jpg",
    .method = HTTP_GET,
    .handler = latest_handler,
    .user_ctx = NULL
  };

  log_i("Starting door camera server on port: '%d'", config.server_port);
  if (httpd_start(&door_httpd, &config) != ESP_OK) {
    log_e("Failed to start door camera server");
    return;
  }

  httpd_register_uri_handler(door_httpd, &index_head_uri);
  httpd_register_uri_handler(door_httpd, &health_uri);
  httpd_register_uri_handler(door_httpd, &trigger_uri);
  httpd_register_uri_handler(door_httpd, &latest_uri);
}

void setupLedFlash() {
#if defined(LED_GPIO_NUM)
  ledcAttach(LED_GPIO_NUM, 5000, 8);
  enable_led(false);
  log_i("LED flash configured on GPIO %d", LED_GPIO_NUM);
#endif
}
