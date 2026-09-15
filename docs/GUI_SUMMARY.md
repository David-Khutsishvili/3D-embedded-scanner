# GUI App: Summary of Work, Decisions and Options

Branch `gui-app` (local only). This document records what was built, how it works, every decision with the options that were considered, and what is left for later.

## 1. Result
- **One desktop app** (`src/gui/scanner_app.py`, PySide6 + PyVista) with two tabs:
  - **Scan:** connect to the scanner (or the built-in simulator), check it is ready, **Start/Stop** a scan, watch progress and an optional live 3D preview. Data is captured and saved automatically.
  - **Viewer:** pick any captured scan and inspect it in interactive 3D (rotate 360°, zoom, pan). A single button toggles **point cloud ⇄ surface mesh**; there is also **Mirror**, **Smooth**, and **PNG screenshot**.
- **Firmware** (`scan.ino`) now idles after boot and is controlled over serial (`PING`/`START`/`STOP`). The scan logic itself is unchanged.
- **Shared Python core** (`src/gui/scanner_core/`) used by both the GUI and the existing CLI scripts, which keep their usage.
- **Reconstruction fix:** the old viewer produced a **mirror image** of the object. It is now correct by default.
- Works on any PC **without hardware**: browse and view the existing scans, or run a full demo scan with the simulator.

## 2. How to run
```bash
# once, from the repo root (code/)
python -m venv .venv
.venv\Scripts\activate            # Windows   (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt

python src/gui/scanner_app.py                     # GUI app
python src/gui/capture.py scans/obj.json --port COM5
python src/gui/scanner_gui.py scans/pear_real.json --mesh
```
- **Firmware:** flash `src/scanner/scan/scan.ino` once (Arduino IDE or `arduino-cli`, see the README). After boot it waits for commands.

## 3. Architecture
```
scan.ino ── USB serial, 115200 baud, text lines ──► ScannerLink
                                                     ├─ SerialLink     (real scanner)
                                                     └─ SimulatedLink  (replays a scan file)
                                                              │
                                      scanner_core (no GUI code)
                   protocol · capture_session · scan_files · reconstruction · visual
                                 │                                  │
               scanner_ui + scanner_app.py (PySide6 + PyVista)   capture.py / scanner_gui.py (CLIs)
```

| Module | Responsibility |
|---|---|
| `scanner_core/protocol.py` | Protocol constants, `Measurement`, `StopPoint`, `parse_line()` → typed `Event` |
| `scanner_core/links.py` | `ScannerLink` interface, `SerialLink` (pyserial), `list_serial_ports()` |
| `scanner_core/simulator.py` | `SimulatedLink`: speaks the firmware protocol by replaying an existing scan |
| `scanner_core/capture_session.py` | `CaptureSession` (collects events, autosaves every 5 s, final status), `wait_for_ready()` |
| `scanner_core/scan_files.py` | Scan `.json` + `.meta.json` load/save (atomic writes), `list_scans()` |
| `scanner_core/reconstruction.py` | Point cloud and closed surface mesh (numpy only) |
| `scanner_core/visual.py` | Conversion to PyVista `PolyData` |
| `scanner_ui/*` | PySide6 window, tabs, serial worker thread, 3D scene helpers |

## 4. Firmware changes (`src/scanner/scan/scan.ino`)
| PC → Arduino | Idle | Scanning (checked between rotation steps) |
|---|---|---|
| `PING` | `PONG` | `BUSY` |
| `START` | runs `scanning_routine()` | `BUSY` |
| `STOP` | `READY` | aborts the scan |

- **Arduino → PC:**
  - `READY` after boot and after every scan (once the platform is reset)
  - `SCAN_START`, measurement lines (unchanged format)
  - `SCAN_STOPPED {"vertical_angle","theta","h"}` (only when stopped)
  - `SCAN_END`
  - the prefixed `SCAN_STOPPED` line is invalid JSON on purpose, so old tools ignore it as a log line
- **Command input:** non-blocking, with a fixed 16-byte buffer (no `String`, no heap).
- **Bug fix:** `reset()` now also resets `rotational_angle` and `d_rotational_angle`. Without it, a second scan in the same power-on started from a stale angle and direction (wrong θ values and servo jumps).
- **Verified:** built on the PC with host g++ and stubbed Arduino/servo/sensor libraries, driven by a serial script. The scan output is **identical** to the original firmware; the command, STOP, BUSY and bad-input scenarios all behave as specified. It still needs a real compile and upload with `arduino-cli`, since the board toolchain isn't installed on the development PC.

## 5. Scan files
- `scans/<name>.json`: **unchanged** format (JSON array of `{"theta","h","dist_a","dist_b"}`), so older scans and tools keep working.
- `scans/<name>.meta.json` (new sidecar): `name`, `status`, `started_at`, `ended_at`, `measurement_count`, `source` (port or `simulator:<file>`), `stop_point`, `notes`, `format_version`.
- **Status values:**

  | Status | Meaning |
  |---|---|
  | `complete` | `SCAN_END` received |
  | `stopped` | `SCAN_STOPPED` + `SCAN_END`; the stop point is saved for "continue later" |
  | `interrupted` | link lost or app closed before `SCAN_END` |
  | `in_progress` | autosave while scanning |
  | `unknown` | legacy scan without a meta file |

## 6. Reconstruction
- **Geometry:** the sensors sit on the x-axis at ±R (R = `SENSOR_DIST`/2 = 100 mm), facing the rotation axis z. A reading `d` gives radius `r = R − d`, and `h` is the height.
- **Rotation direction:** the plate turns **counter-clockwise (seen from above)** as θ grows, so a point seen at world position `w` belongs to the object at `R_z(−θ)·w`:
  - sensor A → angle `−θ`, sensor B → angle `180° − θ`, point = `(r·cos φ, r·sin φ, h)`
- **Mirror fix:** the original `scanner_gui.py` used `R_z(+θ)`. That is a reflection (y → −y), and no rotation can undo it, so the model was the object's mirror image. Full two-sensor coverage doesn't reveal it, because every point is reflected the same way; it only shows on asymmetric objects. The old behaviour is available as **Mirror** (GUI) and `--mirror` (CLI). Verified: `mirror=True` reproduces the old output (max difference 2e-14 mm on both scans).
- **Surface mesh:** the scan is already a grid (rings × angles), so the surface is built directly from it. This is fast (~15 ms for `pear_real`) and needs no heavy library:
  1. group the samples by ring (height) and angle bin; the step size is derived from the data (old scans used 4° or 2°)
  2. average the radius per bin; fill empty bins by circular interpolation (covers dropped points and the 176–180° wedges)
  3. connect neighbouring rings with triangles (wrapping at 360°), and close the top and bottom with fans around the axis
  4. result: a closed, consistently outward-facing surface (verified with 0 open edges and positive volume)

## 7. GUI (`src/gui/scanner_app.py`, `src/gui/scanner_ui/`)
| Module | Contents |
|---|---|
| `main_window.py` | `MainWindow`: tabs and status bar. Safe close: confirm during a scan, then STOP, save, and release threads and plotters. |
| `scan_tab.py` | `ScanController` (scanner state machine, no widgets) and `ScanTab` (device, status, progress, log, live preview) |
| `link_worker.py` | `LinkWorker` runs serial I/O in a `QThread`; `LinkConnection` owns the thread lifecycle. Widgets are only touched in the GUI thread (via signals). |
| `viewer_tab.py` | `ScanBrowser` (list + status badges), `ScanInfoBox`, `ViewerTab` (toolbar + 3D view) |
| `scene.py` | `SceneView`: PyVista `QtInteractor`, colour by height, camera handling, renders only while visible |

**Scan tab: state machine**
```
Disconnected ─Connect─► Connecting… (PING every 1 s) ─PONG/READY─► Ready
Ready ─Start─► fresh PING, PONG within 3 s? ─yes─► START ─► Scanning ─Stop─► Stopping…
Scanning/Stopping ─SCAN_END─► scan saved ─► (platform resets) ─READY─► Ready
no answer for 30 s ─► Not ready (keeps pinging every 3 s)
"Error:" from firmware ─► Error        BUSY ─► "Scanner busy", Stop enabled
link lost / READY during a scan ─► data saved as interrupted
```
- **Scan name:** the default is `scan_YYYY-MM-DD_HH-MM` (`demo_…` with the simulator). It is validated, and existing names prompt before overwriting.
- **Scanning in the background:** a scan keeps running while the Viewer tab is open, and the saved scan is selected in the Viewer automatically.
- **Live 3D preview:** optional checkbox. It redraws at most once per second, and only when new data arrived and the preview is visible.
- **Log:** timestamped, capped at 5000 lines, with `>` for commands and `<` for replies. Individual measurements aren't logged.

**Viewer tab**
- **Scans list** with badges: ✓ complete, ■ stopped, ! interrupted, … in progress, – unknown (legacy). **Refresh** and **Open folder…** (the chosen folder is also used by the Scan tab).
- **Info box:** status, start/end time, measurements, rings, height range, θ range/step, stop point, source.
- **Toolbar:**
  - one button toggles **View: Points ⇄ Mesh**
  - **Mirror**
  - **Smooth** (Laplacian, 50 iterations, relaxation 0.1)
  - **Point size**
  - **Reset view**
  - **Screenshot…** (PNG, default `figures/<scan>_<mode>.png`)
- Toggling mode, mirror or smooth keeps the current camera.

**Choices made during implementation**
- **Smoothing strength:** relaxation 0.1, because pyvista's default (0.01) had no visible effect on the noisy mesh.
- **Default names:** if a default name is already taken, `_2`, `_3`, … is appended.
- **Live preview camera:** each redraw re-fits the zoom to the growing object but keeps the rotation.
- **Scans that never start:** nothing is saved if the scanner never sent `SCAN_START`.
- **Pop-ups:** clear warning dialogs for "cannot connect", "connection lost", "scanner not ready" and "scanner busy".

## 8. Testing
| Area | How | Result |
|---|---|---|
| Firmware | Host g++ build with stubbed Arduino/Servo/VL53L1X libraries, driven by a scripted serial session | Scan output identical to the original firmware. `PING`/`START`/`STOP`/`BUSY`, bad input and a 2nd scan in one power-on all correct. **Not yet compiled for the board** (`arduino-cli` isn't installed on the development PC). |
| Core + CLIs | 219 automated checks | All pass. Covered: protocol parsing, scan file round trip (byte-identical format), simulator, capture statuses, old-vs-new reconstruction (mirror equivalence within 2e-14 mm), closed mesh (0 open edges, outward normals), CLI error handling. |
| GUI | 44 automated checks driving the real window with the simulator, on a copy of the scans folder | All pass. Covered: viewer points/mesh/mirror/smooth; connect → start → stop (saved `stopped` + stop point) → start → complete; scanning while on the Viewer tab; close during a scan saves the data; screenshot; malformed file; state-machine edge cases (legacy auto-start, `Error:`, `BUSY`, not ready, link loss). |
| Real hardware end-to-end | n/a | **Pending:** flash the new firmware and run a real scan from the app. |

## 9. Decisions and options considered
| Topic | Decision | Options considered |
|---|---|---|
| Start a scan from the PC | Small `scan.ino` change: idle + serial commands | Separate sketch (duplicate code); no firmware change (press the reset button) |
| Firmware commands | `START`, `STOP` (keep data + stop point), `PING`/`READY` handshake | Scan settings from the GUI (later); progress lines (later) |
| Stopped scans | Save the data + stop point now | Stop + Continue now (more firmware and merge work); Continue is in the backlog |
| GUI technology | **PySide6 + PyVista (pyvistaqt)** | See the list below |
| 3D display | Point cloud + surface mesh with a toggle button | Point cloud only; Poisson mesh via Open3D (heavy, unpredictable on noisy ToF data) |
| Existing scripts | Shared core; CLIs keep their usage | Minimal edits + duplicated GUI code; remove the CLIs |
| Mirror image | Correct (`−θ`) by default + Mirror toggle | Keep `+θ` (wrong for asymmetric objects) |
| Scan metadata | Sidecar `.meta.json` | Single file `{meta, measurements}` (changes the format) |
| Layout | Tabs: Scan \| Viewer | Single window with side panel |
| Code location | `src/gui/scanner_core`, `src/gui/scanner_ui`, `scanner_app.py` | Installable package (`pyproject.toml`) |
| Extras v1 | Simulator, live preview (optional), PNG screenshot | Model export (later) |

**GUI library options (kept for future reference):**
| Option | Pros | Cons |
|---|---|---|
| **PySide6 + PyVista** (chosen) | Native widgets + VTK 3D (lighting, meshes, picking), builds on the original PyVista viewer, cross-platform, LGPL Qt | Largest install (~250 MB with VTK) |
| PyQt6 + PyVista | Same capabilities | GPL/commercial licence; PySide6 is the official Qt binding |
| PySide6 + pyqtgraph (OpenGL) | Light (~80 MB), fast point clouds | Basic lighting and mesh shading, fewer 3D tools |
| Open3D GUI | 3D viewer + reconstruction algorithms built in | Limited widgets and layout, heavy, Python support lags |
| Polyscope | Very quick point cloud and mesh viewing | Debug-tool look (ImGui), limited custom UI |
| Vispy + Qt | Fast GPU rendering | Low-level, meshes and lighting are more work |
| DearPyGui | Fast immediate-mode GUI | No real 3D mesh viewer |
| Tkinter + matplotlib | No install (Tkinter ships with Python) | Slow, clunky 3D with 16k+ points |
| Browser app (Dash/Plotly, NiceGUI) | Good 3D in the browser | Serial control is awkward, not a desktop app |

## 10. Known limitations
- A sample is saved only if **both** sensors are under `DETECTED_THRESH_MM` (firmware), so a valid one-sided reading is dropped.
- With `MAX_ANGLE 176` the scan covers θ 0–176°; the two 4° wedges are interpolated in the mesh.
- ToF readings are noisy (a few mm), so the raw mesh is rough, and single outlier readings show up as spikes; use **Smooth** for a cleaner look.
- The device list selects the first serial port by default; pick **Simulator (demo)** or the Arduino's port explicitly.
- `SENSOR_DIST` (200 mm) and `dh` (0.36 mm/°) are assumed calibrated; errors scale the model.
- Simulated scans save to the chosen scans folder like real ones; delete demo scans you don't need.

## 11. Backlog (agreed for later)
1. **Continue from stop point:** `START` with a start ring, and the GUI appends to a stopped scan. The stop point is already saved.
2. **Scan settings from the GUI:** step sizes and samples per step, sent before `START`.
3. **Export model:** STL / PLY / OBJ.
4. **Firmware progress/config info lines** (optional extra).
5. **LaTeX report:** add the GUI, new protocol and mirror fix; remove the LaTeX build files from git.
