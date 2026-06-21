# ESP32-CAM Web Streamer

Minimal, self-contained firmware that turns an **AI-Thinker ESP32-CAM** into a live
MJPEG webcam you view in any browser. The board joins your 2.4 GHz WiFi, prints its
IP, and serves a live stream at `http://<ip>/` — no app, no cloud, no account.

```
┌─────────────┐      2.4 GHz WiFi      ┌──────────────┐
│  ESP32-CAM  │ ─────────────────────▶ │  Your router │
│  (OV2640/   │                        └──────┬───────┘
│   OV3660)   │                               │ LAN
└─────────────┘                        ┌──────▼───────┐
   serves MJPEG at  http://<ip>/       │   Browser    │  ◀── live video
                                       └──────────────┘
```

> This repo also documents a **multi-hour real-world debugging journey** to get a
> dead-programmer ESP32-CAM flashed and online. If you're fighting the same
> hardware, read [`docs/troubleshooting.md`](docs/troubleshooting.md) — it will
> probably save you a lot of time.

## Features

- Live **MJPEG stream** at `/stream`, simple viewer page at `/`
- Joins your home WiFi (station mode) — keeps your internet, no captive AP
- Reprints its IP over serial every 3 s, plus a `/stream` endpoint with CORS enabled
- Tuned to run on **weak / USB-bus power** (brownout-hardened — see below)
- Credentials kept out of git via `secrets.h`

## Quick start

### 1. Prerequisites

```bash
# arduino-cli with the ESP32 core (3.3.x)
arduino-cli core install esp32:esp32
# esptool for low-level flashing
pip install esptool        # or: brew install esptool
```

### 2. Set your WiFi

```bash
cp esp32-cam/secrets.h.example esp32-cam/secrets.h
# edit esp32-cam/secrets.h — put your 2.4 GHz SSID + password
```

> **ESP32 only supports 2.4 GHz.** A 5 GHz-only SSID will never connect.

### 3. Flash

If you have a **working** USB-serial connection to the cam (MB shield or a
USB-TTL adapter), the helper script does everything:

```bash
./flash.sh                 # auto-detects the serial port
# or: ./flash.sh /dev/cu.usbserial-0001
```

If your AI-Thinker flash chip is finicky (the common case — see below), flash the
binaries explicitly at **40 MHz / DIO**. See [`docs/flashing.md`](docs/flashing.md).

### 4. View it

After flashing, remove any `IO0→GND` jumper, reset the board, and watch serial — it
prints `>>> Camera ready! Open: http://<ip>/`. Open that URL in your browser.

Can't read the IP? Find the cam on your LAN by its MAC:

```bash
for i in $(seq 1 254); do ping -c1 -W1 192.168.1.$i >/dev/null 2>&1 & done; wait
arp -an | grep -i '<cam-mac>'
```

## Documentation

| Doc | What's in it |
|-----|--------------|
| [`docs/hardware.md`](docs/hardware.md) | Boards, the dead MB-shield problem, using a spare ESP32 as a USB-serial **bridge**, full wiring tables |
| [`docs/flashing.md`](docs/flashing.md) | Why 40 MHz/DIO, exact `esptool` offsets, no-reset flashing through a bridge |
| [`docs/firmware.md`](docs/firmware.md) | How the firmware works, tuning resolution/quality, the brownout/power hardening |
| [`docs/troubleshooting.md`](docs/troubleshooting.md) | **Every failure we hit and how we diagnosed it** — charge-only cables, dead CH340, `invalid header`, brownout boot-loops, WiFi `status=6`, and more |

## Repo layout

```
esp32-cam/
├── esp32-cam.ino          # the firmware
├── secrets.h.example      # copy -> secrets.h, add your WiFi creds (gitignored)
flash.sh                   # one-command compile + upload + serial monitor
docs/                      # the full writeup
```

## License

MIT — see [`LICENSE`](LICENSE).
