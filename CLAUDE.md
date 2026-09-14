# CLAUDE.md: 3D Embedded Scanner

Context and standing decisions for AI-assisted work on this repo.

## Project
- **What:** a B.Sc. Embedded Software course final project (50% of the grade), also a resume project. Authors: Yuval Rubin, David Khutsishvili.
- **Hardware:** Arduino Uno R4 WiFi, 2× VL53L1X ToF sensors on fixed pillars facing each other (`SENSOR_DIST` = 200 mm), 2× MG996R servos driving the elevation lift and 1× MG996R rotating the plate 0–176°.
  - The two opposing sensors plus ~180° of rotation give full 360° coverage.
- **Scan motion:** the plate starts at max elevation and θ=0. Each ring measures while rotating **counter-clockwise (seen from above) as θ increases**, lowers one step, then measures back clockwise (serpentine). The scan stops when a ring has no detections.
- **Data flow:** `scan.ino` → serial JSON lines (`{"theta","h","dist_a","dist_b"}` between `SCAN_START`/`SCAN_END`) → capture → `scans/*.json` (JSON array) → reconstruction → 3D view.
- **Current goal:** one desktop GUI app that
  - starts a scan (on the Arduino, plus capture on the PC),
  - lists and picks captured scans,
  - reconstructs them and shows them in interactive 3D (360° rotate, zoom, pan).
- **Portability:** must run on any PC from a fresh clone, including **without** the scanner or Arduino (browsing and viewing existing scans).
- **Course grading:** functionality, code quality (clean, commented, consistent naming), documentation report, presentation, Q&A. Extra features may earn a bonus.

## Guardrails
- All project code and docs live in `code/`. Never modify anything outside it.
- Firmware (`scan.ino`, `hardware_test/*`) and existing scripts work. Change only what was approved (see the decisions log).
- For anything unclear, ask; don't guess.
- Git: work on local branch `gui-app`. **Commits are local only. Never push or touch GitHub.**
- `.venv` lives in `code/.venv` (git-ignored). Never commit it.
- Execution may be delegated to sub-agents; the main agent reviews everything and owns the final output.

## Decisions log
| # | Topic | Decision | Alternatives considered |
|---|-------|----------|-------------------------|
| 1 | Starting a scan from the GUI | **Small change to `scan.ino`:** the Arduino stays powered and idle and is controlled over serial. `scanning_routine()` logic stays the same. | New separate sketch (duplicated code); no firmware change (user presses the reset button) |
| 1a | Firmware commands (now) | `START`; `STOP` (abort between steps, return to the start position, keep the data captured so far, report the stop position); `PING` → `PONG`/`BUSY` plus `READY` after boot and after each scan (the GUI checks the hardware is ready before sending `START`) | n/a |
| 1b | Firmware fix | `reset()` must also reset `rotational_angle` and `d_rotational_angle`. Otherwise a 2nd scan in the same power-on starts from a stale angle. | n/a |
| 1c | Continue a stopped scan | **Now:** save the data plus the stop point (`vertical_angle`, `theta`, `h`) in the meta file. **Later:** a "Continue from stop point" feature, built together with Scan settings from GUI. | Stop + Continue now (more firmware and merge work) |
| 2 | GUI technology | **PySide6 + PyVista (pyvistaqt).** Implement only this. | PySide6 + pyqtgraph (lighter, basic 3D); Open3D GUI (limited widgets, heavy); Polyscope (debug-tool feel); Tkinter + matplotlib (slow 3D); browser app, Dash/Plotly (awkward serial); document all of these |
| 3 | 3D reconstruction | **Point cloud + surface mesh**, with a **button to toggle** between them | Improved point cloud only; Poisson mesh via Open3D (heavy, unpredictable on noisy ToF data) |
| 4 | Existing Python scripts | **Shared core:** serial protocol and reconstruction move into a shared module used by both the GUI and the CLIs. `capture.py` and `scanner_gui.py` keep working as CLIs, and `capture.py` gains the `START` step. | Minimal edits plus duplicated GUI code; replace the CLIs with the GUI |
| 5 | Rotation direction / mirroring | The plate turns **counter-clockwise** (from above) as θ increases, so the object-frame point is `R(−θ)·w`. The old `scanner_gui.py` used `R(+θ)` (mirror image). **The default is the real object (`−θ`)**, with a **Mirror toggle** in the GUI. The CLIs use the same corrected core. | Keep `+θ` (mirror image, wrong for asymmetric objects) |
| 6 | Scan metadata | **Sidecar `scans/<name>.meta.json`** (status, stop point, dates, source, notes). `scans/<name>.json` stays a plain measurement array. Scans without a meta file still load. | Single file `{"meta", "measurements"}` (changes the format) |
| 7 | Extras (v1, nice-to-have) | **Simulated scanner** (replays an existing scan, for demos and testing without hardware); **live 3D preview while scanning** (optional toggle, non-blocking); **screenshot to PNG** | n/a |
| 8 | GUI layout | **Tabs: Scan \| Viewer.** Scan tab: device, connect, name, Start/Stop, progress, live preview toggle, log. Viewer tab: scans list, info, 3D view with Points/Mesh toggle, Mirror, PNG. | Single window with side panel |
| 9 | Code structure | Python stays in `src/gui/`: `scanner_core/` (shared, no Qt), `scanner_ui/` (PySide6), `scanner_app.py` (GUI entry point). CLIs keep their names. Adds `requirements.txt` and `docs/GUI_SUMMARY.md`. The LaTeX report stays unchanged for now. | Installable package (`pyproject.toml`, `src/scanner3d/`) |
| 10 | Git safety | Branch `gui-app` created from `main` @ `971023d`. Empty checkpoint commit `481dc70` is the revert point. | n/a |
| 11 | Permissions | Allowed: create `code/.venv` + pip install; read `Project Guidelines (1).pdf` and `example.png` in the parent folder (read-only); local commits on `gui-app`. | n/a |

## Backlog (later, not now)
Do these only after the GUI works end-to-end (connect, full scan control, capture, view model).
- **Continue from stop point:** the firmware accepts a start ring with `START`, and the GUI appends to a stopped scan.
- **Scan settings from the GUI:** step sizes and samples per step sent before `START`.
- **Export model** (STL/PLY/OBJ).
- **Progress / config info lines from the firmware:** optional extra, do last.
- **LaTeX report:** update for the GUI, new protocol and mirror fix. Remove the LaTeX build files from git.

## Known limitations (documented, not bugs)
- A point is saved only if **both** sensors are under `DETECTED_THRESH_MM`, so one-sided valid readings are dropped.
- With `MAX_ANGLE 176`, θ covers 0–176°, leaving two 4° wedges unscanned. Older scans (`green_apple`, `pear_real`) used 0–180°.
- Old scans were made with different firmware settings (4° vs 2° steps), so reconstruction must derive step sizes from the data.

## Documentation rule
Record every choice and the options considered, concisely, in `docs/GUI_SUMMARY.md`. This file keeps the short decisions log.
