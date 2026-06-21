# Troubleshooting

A field guide built from an actual end-to-end debugging session. Symptoms first.

## Quick diagnosis table

| Symptom | Most likely cause | Jump to |
|---------|-------------------|---------|
| Device never appears as a serial port; **zero USB events** | Charge-only cable, or dead USB-serial chip | [No serial port](#no-serial-port-at-all) |
| Power LED on, but no `/dev/cu.*` and nothing in USB tree | Charge-only cable **or** dead CH340 on MB shield | [No serial port](#no-serial-port-at-all) |
| `invalid header: 0x00000000` at boot | Flash speed too high (80 MHz) for the cam chip | [invalid header](#invalid-header-0x00000000) |
| App reaches `entry 0x...` then resets, looping, no crash dump | Brownout / weak power | [Brownout boot loop](#brownout-boot-loop) |
| Boots fine but WiFi `status=6`, never connects | Wrong password, or TX power too low to reach AP | [WiFi won't connect](#wifi-wont-connect) |
| `PSRAM not found` / `Camera probe failed 0x106` | Wrong board (no camera/PSRAM) or unseated sensor | [Wrong board](#wrong-board--camera-not-detected) |
| Garbled serial (`怘怘怘…`) | Baud mismatch, or two chips driving the shared UART line | [Garbled serial](#garbled-serial) |

---

## No serial port at all

The host sees **nothing** when you plug the board in — no `/dev/cu.usbserial-*`, and:

```bash
# macOS — is anything even on the USB bus?
ioreg -p IOUSB -w 0 | grep -iv 'AppleT8132USBXHCI\|Root'   # empty = nothing enumerated
# any USB-serial chip?
ioreg -p IOUSB -l -w 0 | grep -iE 'CP210|CH34|Silicon|QinHeng|1a86|10c4'
# did the OS log a connect event at all?
log show --last 2m --predicate 'eventMessage CONTAINS[c] "USB"' | grep -i enumerat
```

**Decision tree:**

1. **Zero USB events / empty bus** → the host's data lines see nothing. This is almost
   always a **charge-only cable** (power wires only). The power LED lighting proves
   *nothing* about data. Swap to a cable you've actually transferred files with, and
   plug **directly into the host**, not a hub.
2. **A driverless chip would still enumerate** (you'd see vendor `0x1a86`/`0x10c4`
   with no `/dev` node). So *truly nothing* on the bus = not a driver problem.
3. Still nothing with a known-data cable, direct to host → the board's **USB-serial
   chip is dead**. For the MB shield, confirm with the **bare shield** (no cam) — if
   it still won't enumerate, the CH340 is gone.

**Fix:** use a [spare ESP32 as a bridge](hardware.md#using-a-spare-esp32-as-a-usb-serial-bridge),
or a standalone CP2102/CH340 USB-TTL adapter. CP2102 is more reliable on macOS (its
driver is built in).

---

## invalid header: 0x00000000

```
rst:0x1 (POWERON_RESET),boot:0xb (HSPI_FLASH_BOOT)
invalid header: 0x00000000
invalid header: 0x00000000
...
```

The ROM bootloader can't read a valid 2nd-stage bootloader at `0x1000`. On AI-Thinker
cams this is a **flash-speed** problem: the build defaulted to **80 MHz** and the
cheap flash chip can't be read that fast *at boot* — even though `esptool` *verified*
the write (it reads back slower than the ROM does at boot).

**Fix:** reflash the individual binaries at **40 MHz / DIO**. See
[flashing.md](flashing.md#3-flash-at-40-mhz--dio). Do **not** just reflash the
`merged.bin` at `0x0` — it carries the 80 MHz setting.

---

## Brownout boot loop

```
entry 0x400805b4        <- app starts
ets Jul 29 2019 ...     <- immediately resets, no panic/backtrace
entry 0x400805b4
ets Jul 29 2019 ...     <- ...repeats forever
```

App reaches its entry point, then resets with **no crash dump** — the classic
ESP32-CAM **brownout**. The moment the firmware powers the camera sensor (and later
the WiFi radio), the current spike collapses a weak supply.

**Fixes, in order of effectiveness:**

1. **Power it properly** — MB shield, 5V charger/power-bank, or bench supply. This is
   the real fix.
2. **Firmware hardening** (lets it survive marginal power):
   ```c
   WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0);  // disable brownout reset
   config.xclk_freq_hz = 10000000;             // lower cam clock
   WiFi.setTxPower(WIFI_POWER_11dBm);          // smaller WiFi TX spike
   ```
   Note: disabling the brownout *detector* only helps with brief dips — a genuine
   voltage collapse still resets the chip. Lowering the camera clock and WiFi TX
   power reduce the actual current draw.

---

## WiFi won't connect

The board boots fine and `loop()` prints, e.g.:

```
>>> WiFi not connected yet (status=6)
```

Add the disconnect-reason handler (already in this firmware) to see *why*:

```
>>> WiFi DISCONNECTED, reason=15    <- 4-way handshake timeout = WRONG PASSWORD
```

### WiFi status & disconnect-reason codes

`WiFi.status()` values:

| status | meaning |
|--------|---------|
| 1 | `WL_NO_SSID_AVAIL` — SSID not seen (wrong name, or 5 GHz-only) |
| 3 | `WL_CONNECTED` ✅ |
| 4 | `WL_CONNECT_FAILED` — often wrong password |
| 6 | `WL_DISCONNECTED` — not associated (auth fail / weak uplink / still trying) |

Common disconnect `reason=` codes:

| reason | meaning |
|--------|---------|
| 15 | 4-way handshake timeout → **wrong password** |
| 2 | auth expired |
| 201 / 205 | no AP found / connection failed |

**Things we actually hit:**

- **ESP32 is 2.4 GHz only.** A `*_5GHz` SSID will never work — use the 2.4 GHz one.
- **Wrong password** is the #1 cause of `status=6`. Double-check exact case and
  symbols (our bug: the password was `name@1970`, not `name123`).
- **TX power set too low** to save power can keep the uplink from reaching the router
  → also `status=6`. Bump `WiFi.setTxPower()` up a notch if power allows.

---

## Wrong board / camera not detected

```
PSRAM chip not found or not supported
Detected camera not supported
Camera probe failed with error 0x106 (ESP_ERR_NOT_SUPPORTED)
```

Means: **no PSRAM and no camera sensor** — i.e. you flashed a **plain ESP32 dev
board**, not the ESP32-CAM. The AI-Thinker cam always has PSRAM (a `PSRAM64H` chip on
the back). Flash the board with the **lens** on it.

If it *is* the cam but the sensor isn't detected, reseat the camera ribbon in its FPC
connector.

> You **cannot** breadboard a bare camera module onto a plain ESP32 — the parallel
> DVP bus (8 data lines + ~10–20 MHz clocks) needs short controlled PCB traces and
> onboard PSRAM. That's why the camera stays on its purpose-built board.

---

## Garbled serial

```
怘怘怘怘怘怘...  (or other repeated nonsense)
```

- **Baud mismatch** — read at `115200`.
- On a **bridge** setup: if the bridge's `EN→GND` jumper comes loose, the bridge's own
  ESP32 wakes up and drives the shared UART line *against* the cam → collision →
  garbage. Reseat `EN→GND`.
- Marginal power can also corrupt the line during WiFi TX bursts. Even when garbled,
  `strings` on the capture often recovers the useful lines:
  ```bash
  strings capture.txt | grep -aiE 'http://|GOT IP|reason|Camera ready'
  ```
  That's how we recovered `>>> Camera ready! Open: http://1...` and then found the
  full IP via [ARP](flashing.md#reading-the-ip-without-serial).

---

## General principle

When stuck, **isolate one variable at a time**: bare board vs. with-cam, known-data
cable vs. unknown, direct host port vs. hub, one wire at a time. Most of the dead ends
above looked like software problems but were cables, power, or a dead chip.
