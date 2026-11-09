#!/usr/bin/env python3
"""Make a track folder (tracks/<id>/) from one analysed day, so the track gets a name and turn
numbers that stay the same on every later day and in both directions.

Usage:
    python3 -I scripts/track_add.py "Track name" output/analysis_<date> [--id ID] [--place "Town, State"]

Run the day first (scripts/run_day.py). This reads that day's viewer.json and corners.csv and writes
    tracks/<id>/track.json   name, centre and radius (from the lap's GPS path)
    tracks/<id>/turns.csv    one row per corner the day found, numbered in riding order
Then edit turns.csv to match the official track map: change the turn numbers, add names, add a row
for a turn the data missed, or delete a row that is not a real turn. Positions only need to be
within about 100 m of the apex. Sessions are matched to the track by GPS from then on.
"""
import argparse
import csv
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tracks  # noqa: E402

MARGIN_M = 1000.0


def main():
    ap = argparse.ArgumentParser(description="Make tracks/<id>/ from one analysed day.")
    ap.add_argument("name", help='the track\'s full name, for example "Eagles Canyon Raceway"')
    ap.add_argument("analysis", help="an analysis folder written by run_day.py")
    ap.add_argument("--id", help="folder name under tracks/ (default: made from the name)")
    ap.add_argument("--place", default="", help="town and state or country")
    ap.add_argument("--tracks-dir", default=tracks.TRACKS_DIR, help=argparse.SUPPRESS)
    a = ap.parse_args()
    try:
        with open(os.path.join(a.analysis, "viewer.json"), encoding="utf-8") as f:
            v = json.load(f)
    except (OSError, ValueError) as e:
        sys.exit(f"{a.analysis}: no usable viewer.json ({e}); run scripts/run_day.py for that day first")
    lat, lon = v["path"]["lat"], v["path"]["lon"]
    clat, clon = (min(lat) + max(lat)) / 2, (min(lon) + max(lon)) / 2
    tid = tracks.slug(a.id or a.name)
    if not tid:
        sys.exit("the track needs a name with letters or digits in it")
    there = tracks.match(clat, clon, a.tracks_dir)
    if there:
        sys.exit(f"these laps are already at a known track: {there['name']} (tracks/{there['id']}/)")
    folder = os.path.join(a.tracks_dir, tid)
    if os.path.exists(folder):
        sys.exit(f"{folder} already exists")
    turns = tracks.read_turns(os.path.join(a.analysis, "corners.csv"))
    if not turns:
        sys.exit(f"{a.analysis}: no corners.csv with corners in it")
    reach = max(tracks.distance_m(clat, clon, la, lo) for la, lo in zip(lat, lon))
    radius = int(math.ceil((reach + MARGIN_M) / 100.0) * 100)
    way = {"CW": "clockwise", "CCW": "counterclockwise"}.get(v.get("direction"), "riding")
    os.makedirs(folder)
    with open(os.path.join(folder, "track.json"), "w", encoding="utf-8") as f:
        json.dump({"name": a.name, "place": a.place,
                   "center": {"lat": round(clat, 4), "lon": round(clon, 4)}, "radius_m": radius,
                   "turns": "turns.csv",
                   "turns_note": f"Numbered in {way} riding order from the timing line; not yet checked against "
                                 "the official track map."}, f, indent=2)
        f.write("\n")
    with open(os.path.join(folder, "turns.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["turn", "name", "lat", "lon"])
        for t in turns:
            w.writerow([t["turn"], t["name"], f"{t['lat']:.6f}", f"{t['lon']:.6f}"])
    print(f"wrote {folder}: {len(turns)} turns, centre {clat:.4f}, {clon:.4f}, radius {radius} m")
    print("Edit turns.csv to match the official map, then update turns_note in track.json.")


if __name__ == "__main__":
    main()
