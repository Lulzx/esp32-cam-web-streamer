// ESP32-CAM (AI-Thinker) — live MJPEG stream + snapshot over WiFi.
// Joins your home WiFi, prints its IP to Serial; open http://<ip>/ in a browser.

#include "esp_camera.h"
#include <WiFi.h>
#include "soc/soc.h"            // brownout register
#include "soc/rtc_cntl_reg.h"  // RTC_CNTL_BROWN_OUT_REG

// ---- WiFi credentials (2.4 GHz only) ----
// Real values live in secrets.h (gitignored). Copy secrets.h.example -> secrets.h
// and fill in your own 2.4 GHz SSID + password.
#include "secrets.h"
const char *WIFI_SSID = WIFI_SSID_VALUE;
const char *WIFI_PASS = WIFI_PASS_VALUE;

// ---- stream tuning (raise these once the cam has a solid 5V supply) ----
//  Frame size: FRAMESIZE_VGA(640x480) SVGA(800x600) XGA(1024x768)
//              HD(1280x720) SXGA(1280x1024) UXGA(1600x1200, OV2640 max)
#define CAM_FRAME_SIZE    FRAMESIZE_UXGA    // init at max; change live via /control?fs=N
#define CAM_JPEG_QUALITY  12                // 0-63, lower number = sharper/bigger; live via /control?q=N
#define CAM_XCLK_HZ       20000000          // 20MHz = max sensor frame rate
#define CAM_WIFI_TX       WIFI_POWER_19_5dBm // max TX = max throughput (needs solid 5V)

// ---- AI-Thinker ESP32-CAM pin map ----
#define PWDN_GPIO_NUM   32
#define RESET_GPIO_NUM  -1
#define XCLK_GPIO_NUM    0
#define SIOD_GPIO_NUM   26
#define SIOC_GPIO_NUM   27
#define Y9_GPIO_NUM     35
#define Y8_GPIO_NUM     34
#define Y7_GPIO_NUM     39
#define Y6_GPIO_NUM     36
#define Y5_GPIO_NUM     21
#define Y4_GPIO_NUM     19
#define Y3_GPIO_NUM     18
#define Y2_GPIO_NUM      5
#define VSYNC_GPIO_NUM  25
#define HREF_GPIO_NUM   23
#define PCLK_GPIO_NUM   22

#include "esp_http_server.h"
#include "esp_timer.h"

#define PART_BOUNDARY "123456789000000000000987654321"
static const char *STREAM_CONTENT_TYPE = "multipart/x-mixed-replace;boundary=" PART_BOUNDARY;
static const char *STREAM_BOUNDARY = "\r\n--" PART_BOUNDARY "\r\n";
static const char *STREAM_PART = "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n";

static httpd_handle_t camera_httpd = NULL;   // port 80: page + control
static httpd_handle_t stream_httpd = NULL;   // port 81: MJPEG stream (separate so control isn't blocked)

static const char INDEX_HTML[] PROGMEM = R"rawliteral(
<!DOCTYPE html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ESP32-CAM</title><style>body{margin:0;background:#111;color:#eee;font-family:system-ui;text-align:center}
img{max-width:100%;height:auto;display:block;margin:0 auto}h3{padding:10px}
.bar{padding:8px;font-size:14px}select,input{vertical-align:middle}</style></head>
<body><h3>ESP32-CAM live</h3>
<div class="bar">res
<select id="fs" onchange="c()">
<option value="5">QVGA 320x240</option><option value="8">VGA 640x480</option>
<option value="9">SVGA 800x600</option><option value="10">XGA 1024x768</option>
<option value="11">HD 1280x720</option><option value="13">UXGA 1600x1200</option></select>
&nbsp;quality <input id="q" type="range" min="4" max="40" value="12" onchange="c()">
</div>
<img id="v">
<script>var h=location.hostname;document.getElementById('v').src='http://'+h+':81/stream';
function c(){fetch('http://'+h+'/control?fs='+fs.value+'&q='+q.value);}</script>
</body></html>
)rawliteral";

static esp_err_t index_handler(httpd_req_t *req) {
  httpd_resp_set_type(req, "text/html");
  return httpd_resp_send(req, INDEX_HTML, strlen(INDEX_HTML));
}

// Runtime tuning: /control?fs=<framesize int>&q=<jpeg quality 4-63>
static esp_err_t control_handler(httpd_req_t *req) {
  sensor_t *s = esp_camera_sensor_get();
  size_t len = httpd_req_get_url_query_len(req) + 1;
  if (len > 1) {
    char *buf = (char *)malloc(len);
    if (buf && httpd_req_get_url_query_str(req, buf, len) == ESP_OK) {
      char v[12];
      if (httpd_query_key_value(buf, "fs", v, sizeof(v)) == ESP_OK) s->set_framesize(s, (framesize_t)atoi(v));
      if (httpd_query_key_value(buf, "q",  v, sizeof(v)) == ESP_OK) s->set_quality(s, atoi(v));
    }
    free(buf);
  }
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  httpd_resp_set_type(req, "text/plain");
  char out[48];
  int n = snprintf(out, sizeof(out), "fs=%d q=%d\n", s->status.framesize, s->status.quality);
  return httpd_resp_send(req, out, n);
}

static esp_err_t stream_handler(httpd_req_t *req) {
  camera_fb_t *fb = NULL;
  esp_err_t res = httpd_resp_set_type(req, STREAM_CONTENT_TYPE);
  if (res != ESP_OK) return res;
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  char part_buf[64];
  while (true) {
    fb = esp_camera_fb_get();
    if (!fb) { res = ESP_FAIL; break; }
    res = httpd_resp_send_chunk(req, STREAM_BOUNDARY, strlen(STREAM_BOUNDARY));
    if (res == ESP_OK) {
      size_t hlen = snprintf(part_buf, sizeof(part_buf), STREAM_PART, fb->len);
      res = httpd_resp_send_chunk(req, part_buf, hlen);
    }
    if (res == ESP_OK)
      res = httpd_resp_send_chunk(req, (const char *)fb->buf, fb->len);
    esp_camera_fb_return(fb);
    if (res != ESP_OK) break;
  }
  return res;
}

static void start_server() {
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  httpd_uri_t index_uri  = { .uri = "/",        .method = HTTP_GET, .handler = index_handler,   .user_ctx = NULL };
  httpd_uri_t ctrl_uri   = { .uri = "/control", .method = HTTP_GET, .handler = control_handler, .user_ctx = NULL };
  httpd_uri_t stream_uri = { .uri = "/stream",  .method = HTTP_GET, .handler = stream_handler,  .user_ctx = NULL };

  config.server_port = 80; config.ctrl_port = 32768;
  if (httpd_start(&camera_httpd, &config) == ESP_OK) {
    httpd_register_uri_handler(camera_httpd, &index_uri);
    httpd_register_uri_handler(camera_httpd, &ctrl_uri);
  }
  config.server_port = 81; config.ctrl_port = 32769;
  if (httpd_start(&stream_httpd, &config) == ESP_OK) {
    httpd_register_uri_handler(stream_httpd, &stream_uri);
  }
}

void setup() {
  WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0);  // disable brownout reset (weak-power fix)
  Serial.begin(115200);
  Serial.setDebugOutput(true);
  Serial.println();
  delay(200);
  Serial.println(">>> BOOT: esp32-cam firmware starting");

  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM; config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM; config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM; config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM; config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = CAM_XCLK_HZ;
  config.frame_size = psramFound() ? CAM_FRAME_SIZE : FRAMESIZE_VGA;  // no PSRAM -> stay small
  config.pixel_format = PIXFORMAT_JPEG;
  config.grab_mode = CAMERA_GRAB_LATEST;   // always serve the freshest frame -> minimal lag
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.jpeg_quality = CAM_JPEG_QUALITY;
  config.fb_count = psramFound() ? 3 : 1;  // 3 buffers (PSRAM) = headroom vs FB-OVF, drives latest-frame drop

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed: 0x%x\n", err);
    return;
  }
  // init at UXGA allocates max-size buffers; drop to a smooth boot default.
  // Slide live from the page UI (or /control?fs=N&q=M): QVGA=5..UXGA=13.
  sensor_t *s = esp_camera_sensor_get();
  s->set_framesize(s, FRAMESIZE_SVGA);   // ~15 FPS, sharp default
  s->set_quality(s, 12);

  WiFi.onEvent([](WiFiEvent_t e, WiFiEventInfo_t info){
    Serial.print(">>> WiFi DISCONNECTED, reason=");
    Serial.println(info.wifi_sta_disconnected.reason);
  }, ARDUINO_EVENT_WIFI_STA_DISCONNECTED);
  WiFi.onEvent([](WiFiEvent_t e, WiFiEventInfo_t info){
    Serial.println(">>> WiFi GOT IP (associated)");
  }, ARDUINO_EVENT_WIFI_STA_GOT_IP);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  WiFi.setSleep(false);
  WiFi.setTxPower(CAM_WIFI_TX);         // see CAM_WIFI_TX define
  Serial.println(">>> WiFi.begin called; watching status in loop()");

  start_server();   // server starts now; reachable once WiFi associates
}

void loop() {
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print(">>> Camera ready! Open: http://");
    Serial.print(WiFi.localIP());
    Serial.println("/");
  } else {
    Serial.print(">>> WiFi not connected yet (status=");
    Serial.print(WiFi.status());
    Serial.println(")");
  }
  delay(3000);
}
