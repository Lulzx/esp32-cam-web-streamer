#!/usr/bin/env bash
# Flash the ESP32-CAM firmware and show the camera's IP.
#
# Usage:  ./flash.sh [serial-port]
#   - With no argument it auto-detects a CP2102 / CH340 / generic USB-serial port.
#   - Pass a port explicitly to override, e.g.  ./flash.sh /dev/cu.SLAB_USBtoUART
#
# Before flashing: wire IO0 -> GND on the cam to enter flash mode, then press RESET
# (or power-cycle). After a successful flash, remove the IO0 jumper and press RESET.

set -euo pipefail

FQBN="esp32:esp32:esp32cam"
SKETCH_DIR="$(cd "$(dirname "$0")" && pwd)/esp32-cam"

# --- locate the serial port ---
PORT="${1:-}"
if [[ -z "$PORT" ]]; then
  for cand in /dev/cu.SLAB_USBtoUART /dev/cu.usbserial-* /dev/cu.wchusbserial* /dev/cu.usbmodem*; do
    if [[ -e "$cand" ]]; then PORT="$cand"; break; fi
  done
fi

if [[ -z "$PORT" || ! -e "$PORT" ]]; then
  echo "ERROR: no USB-serial port found." >&2
  echo "Plug in the CP2102/CH340 adapter and check with:  ls /dev/cu.*" >&2
  exit 1
fi
echo ">>> Using serial port: $PORT"

# --- ensure WiFi credentials exist (secrets.h) ---
if [[ ! -f "$SKETCH_DIR/secrets.h" ]]; then
  echo "ERROR: $SKETCH_DIR/secrets.h not found." >&2
  echo "       cp $SKETCH_DIR/secrets.h.example $SKETCH_DIR/secrets.h  and add your WiFi creds." >&2
  exit 1
fi
if grep -q 'YOUR_2.4GHZ_SSID\|YOUR_WIFI_PASSWORD' "$SKETCH_DIR/secrets.h"; then
  echo "WARNING: secrets.h still contains placeholder credentials." >&2
  read -r -p "Continue anyway? [y/N] " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]] || exit 1
fi

# --- compile + upload ---
echo ">>> Compiling and uploading..."
arduino-cli compile --fqbn "$FQBN" --upload -p "$PORT" "$SKETCH_DIR"

echo
echo ">>> Flash complete. Remove the IO0->GND jumper and press RESET on the cam."
echo ">>> Watching serial for the camera IP (Ctrl-C to stop)..."
echo "------------------------------------------------------------"
# Print serial output; highlight the line that contains the URL.
arduino-cli monitor -p "$PORT" -c baudrate=115200 2>/dev/null | while IFS= read -r line; do
  echo "$line"
  if [[ "$line" == *"http://"* ]]; then
    echo "============================================================"
    echo ">>> OPEN THIS IN YOUR BROWSER:  ${line#*Open: }"
    echo "============================================================"
  fi
done
