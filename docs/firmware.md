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

Defaults are tuned conservative for weak power. To raise quality once you have a
solid 5V supply, edit `setup()`:

| Setting | Default here | Notes |
|---------|--------------|-------|
| `config.xclk_freq_hz` | `10000000` (10 MHz) | Raise to `20000000` for higher frame rate **if** power is solid |
| `config.frame_size` | `FRAMESIZE_VGA` (640×480) | `FRAMESIZE_SVGA`/`XGA`/`SXGA` need PSRAM + good power |
| `config.jpeg_quality` | `12` | Lower number = higher quality = more bandwidth/RAM |
| `config.fb_count` | `2` if PSRAM else `1` | More buffers = smoother stream |

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
