# 3D Embedded Scanner

A DIY 3D scanner built on an Arduino Uno R4 WiFi. Two time-of-flight distance
sensors sit fixed on a pair of pillars, aimed inward at the object. The
object itself sits on a plate that both rotates and is lowered in elevation,
sweeping it past the sensors and tracing out a ring of distance samples. A
small Python toolchain then turns that stream of `(theta, height, distance)`
samples into a 3D point cloud.

## How it works

**Hardware**

- 1x Arduino Uno R4 WiFi
- 2x Adafruit VL53L1X time-of-flight distance sensors (`vl53_a`, `vl53_b`),
  mounted a fixed `SENSOR_DIST` apart on two fixed pillars, looking inward at
  the object
- 3x MG996R servos:
  - `servo1` / `servo2` — lower the object plate's elevation in tandem
  - `servo3` — rotates the plate the object is mounted on

**Sensor bring-up (`init_sensors`)**

Both VL53L1X boards boot at the same default I2C address (0x29), so they'd
collide if powered on together. Each sensor's XSHUT pin is held low to keep
it powered off; sensors are then woken one at a time — sensor A first, which
is immediately reassigned to address 0x30, freeing up 0x29 for sensor B.

**Scan routine (`scanning_routine`)**

The plate starts at maximum elevation and 0° rotation. For each elevation
ring, the plate steps around in `ROTATION_STEP_MAG`-degree increments; at
each step both sensors are sampled (`MEASURE_COUNT` reads, averaged) and, if
both report an object closer than `DETECTED_THRESH_MM`, a JSON measurement
line is printed over serial:

```json
{"theta":42,"h":12.960,"dist_a":88,"dist_b":91}
```

The rotation direction alternates each ring (serpentine) so the plate never
has to snap back to 0°, and the plate is lowered in elevation (`D_VERTICAL`
degrees, converted to a height `h` via `dh`) after each completed ring. The
scan stops early once a ring comes back with no detections at all — the
object has been scanned top to bottom. Output is bracketed by `SCAN_START`
and `SCAN_END` markers so a listener knows when a scan begins and ends.


## Usage

### 1. Flash the scanner

Wire up the two VL53L1X sensors (XSHUT on D2/D3, shared I2C on A4/A5) and the
three servos (D8, D9, D10 — see the `#define`s at the top of `scan.ino`), then
compile and upload `src/scanner/scan/scan.ino` with `arduino-cli` (or the
Arduino IDE) targeting your board, e.g. an Arduino Uno R4 WiFi:

```bash
arduino-cli compile --fqbn arduino:renesas_uno:unor4wifi src/scanner/scan/scan.ino
arduino-cli upload -p /dev/ttyACM0 --fqbn arduino:renesas_uno:unor4wifi src/scanner/scan/scan.ino
```

The `Adafruit_VL53L1X` and `Servo` Arduino libraries are required.

### 2. Capture a scan

With the board plugged in and running, capture its serial output to a JSON
file of measurements:

```bash
python src/gui/capture.py scans/my_object.json --port /dev/ttyACM0
```

This waits for the `SCAN_START` marker, collects every measurement line until
`SCAN_END`, and writes them out as a JSON array. Interrupting with Ctrl-C
saves whatever was captured so far.

### 3. Visualize the point cloud

```bash
python src/gui/scanner_gui.py scans/my_object.json --color "#FF8800"
```

Each measurement's `theta`/`h`/`dist_a`/`dist_b` is converted into two 3D
points (one per sensor) and rendered as a point cloud with PyVista. Pass
`--no-plot` to just print the computed coordinates instead of opening a
viewer.

## Python dependencies

- `pyserial`
- `numpy`
- `pyvista`

Install into a virtualenv with:

```bash
pip install pyserial numpy pyvista
```
