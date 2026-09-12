import argparse
import json

import serial

DEFAULT_BAUD = 115200
SCAN_START = "SCAN_START"
SCAN_END = "SCAN_END"


def capture(port, baud, output_path):
    """
    Read scan.ino's serial output (JSON Lines, one measurement per line,
    bracketed by SCAN_START/SCAN_END markers) and save it as a single JSON
    array at output_path -- matching the format scanner_gui.coords() expects.
    """
    measurements = []
    with serial.Serial(port, baud, timeout=1) as ser:
        print(f"Listening on {port} @ {baud}... waiting for {SCAN_START}")
        try:
            while True:
                raw = ser.readline().decode("utf-8", errors="replace").strip()
                if not raw:
                    continue

                if raw == SCAN_START:
                    print("Scan started.")
                    continue
                if raw == SCAN_END:
                    print(f"Scan finished, {len(measurements)} measurements captured.")
                    break

                try:
                    measurements.append(json.loads(raw))
                except json.JSONDecodeError:
                    # Non-measurement line (e.g. a WARN: log from the sketch) -
                    # surface it but don't let it break the capture.
                    print(f"(ignored) {raw}")
        except KeyboardInterrupt:
            print(f"\nInterrupted, saving {len(measurements)} measurements captured so far.")

    with open(output_path, "w") as f:
        json.dump(measurements, f, indent=2)
        f.write("\n")

    print(f"Saved to {output_path}")


def parse_args():
    parser = argparse.ArgumentParser(description="Capture scan.ino's serial output and save it as JSON.")
    parser.add_argument("output_path", help="Path to write the captured measurements JSON to.")
    parser.add_argument("--port", required=True, help="Serial port the scanner is connected on (e.g. /dev/ttyACM0).")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD, help=f"Serial baud rate (default: {DEFAULT_BAUD}).")
    return parser.parse_args()


def main():
    args = parse_args()
    capture(args.port, args.baud, args.output_path)


if __name__ == "__main__":
    main()
