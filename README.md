# 3D Embedded Scanner

A DIY 3D scanner built on an Arduino Uno R4 WiFi. Two time-of-flight distance
sensors sit fixed on a pair of pillars, aimed inward at the object. The
object itself sits on a plate that both rotates and is lowered in elevation,
sweeping it past the sensors and tracing out a ring of distance samples. A
Python desktop app on the PC starts the scan, captures the stream of
`(theta, height, distance)` samples, and reconstructs it into an interactive
3D point cloud or surface mesh.

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

**Serial commands**

After boot the Arduino resets the platform, prints `READY` and waits for
commands from the PC (115200 baud, one command per line):

| Command | Idle | While scanning |
|---------|------|----------------|
| `PING`  | `PONG` | `BUSY` |
| `START` | runs a scan, then resets the platform and prints `READY` | `BUSY` |
| `STOP`  | `READY` | aborts between rotation steps and prints `SCAN_STOPPED {"vertical_angle":…,"theta":…,"h":…}` before `SCAN_END` |

**Reconstruction**

The sensors face each other, so rotating the plate through ~180° covers the
whole object. Each distance becomes a radius from the rotation axis; because
the plate turns counter-clockwise (seen from above) as `theta` grows, points
are rotated by `-theta` into the object's frame. The scan's rings are also
stitched directly into a closed surface mesh. See
[`docs/GUI_SUMMARY.md`](docs/GUI_SUMMARY.md) for the details and design
decisions.

## Usage

### 1. Set up Python (once)

Python 3.10+ is required. From the repository root:

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # Linux / macOS
pip install -r requirements.txt
```

### 2. Flash the scanner

Wire up the two VL53L1X sensors (XSHUT on D2/D3, shared I2C on A4/A5) and the
three servos (D8, D9, D10 — see the `#define`s at the top of `scan.ino`), then
compile and upload `src/scanner/scan/scan.ino` with `arduino-cli` (or the
Arduino IDE) targeting your board, e.g. an Arduino Uno R4 WiFi:

```bash
arduino-cli compile --fqbn arduino:renesas_uno:unor4wifi src/scanner/scan/scan.ino
arduino-cli upload -p /dev/ttyACM0 --fqbn arduino:renesas_uno:unor4wifi src/scanner/scan/scan.ino
```

The `Adafruit_VL53L1X` and `Servo` Arduino libraries are required.

### 3. Run the desktop app

```bash
python src/gui/scanner_app.py
```

- **Scan tab:** pick the scanner's serial port and connect. Once the status
  shows *Ready*, enter a name and press **Start**. Progress and an optional
  live 3D preview are shown while scanning; **Stop** aborts and still saves the
  data. Scans are saved to `scans/<name>.json` plus a `scans/<name>.meta.json`
  sidecar (status, times, stop point).
- **Viewer tab:** select a scan to view it in 3D (drag to rotate, scroll to
  zoom). Toggle **Points / Mesh**, **Mirror**, **Smooth**, or save a
  **Screenshot**.
- **No hardware?** Choose **Simulator (demo)** as the device to replay an
  existing scan through the full Start → capture → view flow.

### 4. Command-line tools (optional)

Capture a scan without the GUI (handshakes with the scanner, sends `START`;
Ctrl-C sends `STOP` and saves what was captured):

```bash
python src/gui/capture.py scans/my_object.json --port /dev/ttyACM0
```

Show a scan in a standalone PyVista window (`--mesh` for the surface,
`--mirror` for the legacy mirror-image reconstruction, `--no-plot` to print
the coordinates instead):

```bash
python src/gui/scanner_gui.py scans/my_object.json --color "#FF8800"
```

## Project layout

```
docs/                  design notes and decisions (GUI_SUMMARY.md)
figures/               LaTeX project report
scans/                 captured scans (*.json + *.meta.json)
src/scanner/scan/      Arduino scan firmware (scan.ino)
src/scanner/hardware_test/   standalone bring-up sketches
src/gui/scanner_app.py desktop app entry point
src/gui/scanner_ui/    PySide6 user interface
src/gui/scanner_core/  shared protocol, capture, scan files, reconstruction
src/gui/capture.py     CLI capture tool
src/gui/scanner_gui.py CLI viewer
```

## Python dependencies

`numpy`, `pyserial`, `pyvista`, `pyvistaqt`, `PySide6` — see
[`requirements.txt`](requirements.txt).
