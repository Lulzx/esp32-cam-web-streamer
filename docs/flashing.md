# Flashing

## TL;DR

AI-Thinker ESP32-CAM flash chips are often unreliable at the Arduino default of
**80 MHz**. Flash at **40 MHz / DIO** and the `invalid header` boot failures go away.

## The easy path

If you have a working serial connection (port enumerates as `/dev/cu.usbserial-*` or
`/dev/cu.SLAB_USBtoUART`):

```bash
./flash.sh            # auto-detect port, compile, upload, then show the IP
```

## The reliable path (explicit binaries at 40 MHz)

Use this when the cam is flashed through a [bridge](hardware.md#using-a-spare-esp32-as-a-usb-serial-bridge)
or whenever you hit `invalid header`.

### 1. Build to a known directory

```bash
arduino-cli compile --fqbn esp32:esp32:esp32cam \
  --output-dir /tmp/cambuild ./esp32-cam
```

This produces:

| File | Flash offset |
|------|--------------|
| `esp32-cam.ino.bootloader.bin` | `0x1000` |
| `esp32-cam.ino.partitions.bin` | `0x8000` |
| `boot_app0.bin` *(from the esp32 core `tools/partitions/`)* | `0xe000` |
| `esp32-cam.ino.bin` | `0x10000` |

### 2. Put the cam in download mode

Jumper the cam's `IO0 → GND`, then power-cycle / tap `RST`. Verify:

```bash
esptool --port /dev/cu.usbserial-0001 --before no_reset --after no_reset chip_id
# -> Chip type: ESP32-D0WD-V3 ...   (and a MAC that is the CAM's, not the bridge's)
```

### 3. Flash at 40 MHz / DIO

```bash
BOOT_APP0=~/Library/Arduino15/packages/esp32/hardware/esp32/3.3.10/tools/partitions/boot_app0.bin

esptool --port /dev/cu.usbserial-0001 --baud 230400 \
  --before no_reset --after no_reset \
  write_flash --flash_mode dio --flash_freq 40m --flash_size 4MB \
  0x1000  /tmp/cambuild/esp32-cam.ino.bootloader.bin \
  0x8000  /tmp/cambuild/esp32-cam.ino.partitions.bin \
  0xe000  "$BOOT_APP0" \
  0x10000 /tmp/cambuild/esp32-cam.ino.bin
```

### 4. Run it

Remove the `IO0→GND` jumper, reset, and read serial @ 115200. You should see the
boot log, camera init, WiFi association, then `>>> Camera ready! Open: http://<ip>/`.

## Why not `merged.bin` at `0x0`?

`arduino-cli` emits a `*.merged.bin` you *can* flash at `0x0` — but it bakes in the
build's **80 MHz** flash setting. On a finicky cam chip the ROM bootloader then can't
read the 2nd-stage bootloader at boot and prints `invalid header: 0x00000000`, even
though `esptool` *verified* the write (esptool reads back more slowly than the ROM
does at boot). Flashing the individual binaries with `--flash_freq 40m` patches the
bootloader header to 40 MHz and fixes it.

## No-reset flashing through a bridge

When flashing through a spare-ESP32 bridge, the bridge's DTR/RTS auto-reset lines go
to the *bridge's* `EN`/`IO0`, **not the cam's**. So:

- Put the cam in download mode **manually** (`IO0→GND` + power-cycle).
- Always pass `--before no_reset --after no_reset` so esptool doesn't rely on
  auto-reset it can't drive.
- Keep the baud modest (`230400`) — hand-wired breadboard links get flaky higher.

## Reading the IP without serial

Serial output can be noisy on a marginal power/bridge setup. Once the cam is on WiFi,
just find it on the LAN by MAC:

```bash
for i in $(seq 1 254); do ping -c1 -W1 192.168.1.$i >/dev/null 2>&1 & done; wait
arp -an | grep -i '<cam-mac>'
```
