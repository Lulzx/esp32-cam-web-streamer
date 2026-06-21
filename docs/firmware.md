# Firmware

Single sketch: [`esp32-cam/esp32-cam.ino`](../esp32-cam/esp32-cam.ino). It inits the
camera, joins WiFi in station mode, and serves a tiny HTTP server.

## Endpoints

| Path | Response |
|------|----------|
| `/` | Minimal HTML viewer page (an `<img src="/stream">`) |
| `/stream` | `multipart/x-mixed-replace` MJPEG stream, `Access-Control-Allow-Origin: *` |

## Serial output

At 115200 baud:

- `>>> BOOT: ...` once at startup
- `>>> WiFi DISCONNECTED, reason=N` on each failed association (diagnostic — see
  [troubleshooting](troubleshooting.md#wifi-status--disconnect-reason-codes))
- `>>> WiFi GOT IP (associated)` on success
- Every 3 s from `loop()`: either `>>> Camera ready! Open: http://<ip>/` or
  `>>> WiFi not connected yet (status=N)`

The periodic reprint means you can attach a serial monitor *any time* and read the IP
— no need to catch the one-shot boot message.

## Credentials

`WIFI_SSID` / `WIFI_PASS` are pulled from `secrets.h` (gitignored). Copy
`secrets.h.example` → `secrets.h` and fill in your **2.4 GHz** network.

## Camera config & tuning

Tuning lives in `#define`s at the top of the sketch:

| `#define` | Default | Notes |
|-----------|---------|-------|
| `CAM_FRAME_SIZE` | `FRAMESIZE_SVGA` (800×600) | `VGA`/`SVGA`/`XGA`/`HD`/`SXGA`/`UXGA` — higher = sharper but more WiFi load |
| `CAM_JPEG_QUALITY` | `12` | Lower number = higher quality = bigger frames / more bandwidth |
| `CAM_XCLK_HZ` | `20000000` (20 MHz) | Higher = more FPS / less lag; drop to `10000000` if power is weak |
| `CAM_WIFI_TX` | `WIFI_POWER_15dBm` | Higher = more throughput; lower if it brownouts |
| `config.grab_mode` | `CAMERA_GRAB_LATEST` | **always serves the newest frame** so video can't fall behind (the key anti-lag setting) |
| `config.fb_count` | `2` if PSRAM else `1` | Double-buffer: capture while transmitting |

### Lag vs. resolution vs. power

These pull against each other. The lag you perceive is usually frames *queuing up*,
not low FPS — so `CAMERA_GRAB_LATEST` (drop stale frames, send the freshest) is the
biggest fix and costs no power. Higher resolution / clock / TX power reduce lag from
the *throughput* side but draw more current — only push them with a **solid 5V
supply**. On weak power, keep `CAM_XCLK_HZ` at 10 MHz and `CAM_FRAME_SIZE` at VGA.

> **`cam_hal: FB-OVF`** in the serial log = frame-buffer overflow: the sensor is
> producing frames faster than they're drained. Mitigate by lowering `CAM_XCLK_HZ`,
> dropping `CAM_FRAME_SIZE`, or raising `jpeg_quality`'s number (smaller frames).

The pin map is the standard **AI-Thinker** layout; `esp_camera` auto-detects the
sensor (OV2640, OV3660, …) so no change is needed per sensor.

## Power hardening (why it runs on weak supplies)

Three deliberate choices let it survive USB-bus / bridge power that would otherwise
reset it (see [troubleshooting](troubleshooting.md#brownout-boot-loop)):

```c
WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0);   // disable brownout reset
config.xclk_freq_hz = 10000000;              // lower camera clock = less current
WiFi.setTxPower(WIFI_POWER_11dBm);           // smaller WiFi TX current spike
```

Trade-offs: disabling brownout only helps with *marginal* dips (a real collapse still
resets the chip); a lower TX power reduces range. If your supply is solid, you can
revert these for better range and frame rate.

## Static IP (optional)

The router assigns the IP via DHCP, so it can change. For a stable address, either
set a **DHCP reservation** on your router for the cam's MAC, or add a
`WiFi.config(ip, gateway, subnet)` call before `WiFi.begin()`.
