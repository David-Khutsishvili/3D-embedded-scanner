import json
import argparse
import numpy as np
import pyvista as pv

SENSOR_DIST = 200 # 20cm, 200mm

def xy_rotation(theta):
    c, s = np.cos(theta), np.sin(theta)
    return np.array([
        [c, -s, 0],
        [s,  c, 0],
        [0,  0, 1]])


def transform(measurement):
    theta = np.deg2rad(measurement["theta"])
    h = measurement["h"]
    dist_a = measurement["dist_a"]
    dist_b = measurement["dist_b"]
    coord_a = np.array([SENSOR_DIST / 2 - dist_a, 0, h])
    coord_b = np.array([-SENSOR_DIST / 2 + dist_b, 0, h])
    R = xy_rotation(theta)
    return R @ coord_a, R @ coord_b


def coords(measurements_path):
    _coords = []
    with open(measurements_path) as f:
        measurements = json.load(f)
    for measurement in measurements:
        coord_a, coord_b = transform(measurement)
        _coords.append(coord_a)
        _coords.append(coord_b)
    return _coords


def plot_coords(_coords, color="#FFFFFF"):
    points = np.array(_coords)
    cloud = pv.PolyData(points)
    plotter = pv.Plotter()
    plotter.add_mesh(cloud, color=color, point_size=5, render_points_as_spheres=True)
    plotter.add_axes()
    plotter.show()


def hex_color(value):
    value = value if value.startswith("#") else f"#{value}"
    if len(value) != 7:
        raise argparse.ArgumentTypeError(f"invalid hex color: {value!r}")
    try:
        int(value[1:], 16)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid hex color: {value!r}")
    return value


def parse_args():
    parser = argparse.ArgumentParser(description="Load scanner measurements and plot the resulting point cloud.")
    parser.add_argument("measurements_path", help="Path to the JSON measurements file.")
    parser.add_argument("--no-plot", action="store_true", help="Skip plotting; just load and print the coords.")
    parser.add_argument("--color", type=hex_color, default="#FFFFFF", help="Point color as a hex code (e.g. #FF8800).")
    return parser.parse_args()


def main():
    args = parse_args()
    _coords = coords(args.measurements_path)
    if args.no_plot:
        print(_coords)
    else:
        plot_coords(_coords, color=args.color)


if __name__ == "__main__":
    main()




