"""Track definitions, so the tools work at any circuit (standard library only).

A known track is a folder tracks/<id>/ holding track.json and, optionally, a turn table and other
reference files. A session is matched to a track by where its GPS points are, never by the
logger's track name, which is often left on another setting. A track with no folder still works:
its corners are numbered T1, T2... in riding order from the timing line.

track.json
    name                 full name, shown in reports and the viewer
    place                town and state or country (optional, for people reading the file)
    center               {"lat": .., "lon": ..}: about the middle of the circuit; also the map origin
    radius_m             a session whose GPS points centre within this distance is at this track
    turns                file name of the turn table (optional): CSV with turn,name,lat,lon, one
                         row per turn, at or near its apex
    turns_note           one line on how the turns are numbered (optional)
    logger_track_names   {"<AiM track name>": "CW" or "CCW"} (optional): the direction each logger
                         track name implies, used only as a cross-check against the GPS direction
    chicanes             temporary cone chicanes to cost out (optional): see xrk_chicane.py

scripts/track_add.py writes a new track folder from one analysed day.
"""
import csv
import json
import math
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
TRACKS_DIR = os.path.join(os.path.dirname(HERE), "tracks")
DEFAULT_RADIUS_M = 2500.0
SAME_SITE_M = 3000.0      # two unknown-track sessions this close are at the same circuit


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def distance_m(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * 6371000.0 * math.asin(min(1.0, math.sqrt(a)))


def _load(folder):
    path = os.path.join(folder, "track.json")
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        lat0, lon0 = float(d["center"]["lat"]), float(d["center"]["lon"])
        name = str(d["name"])
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise SystemExit(f"{path}: not a usable track file ({type(e).__name__}: {e})")
    turns = os.path.join(folder, d["turns"]) if d.get("turns") else ""
    if turns and not os.path.exists(turns):
        raise SystemExit(f"{path}: turn table {d['turns']} is missing")
    return {"id": os.path.basename(folder), "name": name, "known": True, "place": d.get("place", ""),
            "lat0": lat0, "lon0": lon0, "radius_m": float(d.get("radius_m", DEFAULT_RADIUS_M)),
            "dir": folder, "turns_csv": turns, "turns_note": d.get("turns_note", ""),
            "logger_track_names": {str(k).strip().upper(): v for k, v in (d.get("logger_track_names") or {}).items()},
            "chicanes": list(d.get("chicanes") or [])}


def known(tracks_dir=None):
    """Every track that has a folder, by id."""
    root = tracks_dir or TRACKS_DIR
    if not os.path.isdir(root):
        return []
    return [_load(os.path.join(root, d)) for d in sorted(os.listdir(root))
            if os.path.exists(os.path.join(root, d, "track.json"))]


def match(lat, lon, tracks_dir=None):
    """The nearest known track whose centre is within its radius of this point, or None."""
    best = None
    for t in known(tracks_dir):
        d = distance_m(lat, lon, t["lat0"], t["lon0"])
        if d <= t["radius_m"] and (best is None or d < best[0]):
            best = (d, t)
    return best[1] if best else None


def unknown(lat, lon, venue=""):
    """A track with no folder: named after the logger's track name, origin at this point."""
    name = (venue or "").strip()
    return {"id": slug(name) or "unknown-track", "name": name or "Unknown track", "known": False, "place": "",
            "lat0": float(lat), "lon0": float(lon), "radius_m": DEFAULT_RADIUS_M, "dir": "", "turns_csv": "",
            "turns_note": "", "logger_track_names": {}, "chicanes": []}


def find(lat, lon, venue="", tracks_dir=None):
    return match(lat, lon, tracks_dir) or unknown(lat, lon, venue)


def read_turns(path):
    """Turn positions from a turn table (turn,name,lat,lon) or from the corners.csv the ideal-lap
    step writes (corner,...,apex_lat,apex_lon). Each: turn number, name, label, lat, lon."""
    if not path or not os.path.exists(path):
        return []
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if "apex_lat" in r:
                m = re.match(r"T(\d+)\s*(.*)", r["corner"])
                if not m:
                    continue
                num, name, lat, lon = int(m.group(1)), m.group(2).strip(), r["apex_lat"], r["apex_lon"]
            else:
                num, name, lat, lon = int(r["turn"]), (r.get("name") or "").strip(), r["lat"], r["lon"]
            out.append({"turn": num, "name": name, "label": f"T{num}" + (f" {name}" if name else ""),
                        "lat": float(lat), "lon": float(lon)})
    return out


def turn_table(track):
    """The track's own turn table (official numbers), or [] when it has none."""
    return read_turns(track.get("turns_csv"))


def chicane(track, direction):
    """The cone chicane this track defines for a direction ("CW" or "CCW"), or None."""
    return next((c for c in track.get("chicanes", []) if c.get("direction") == direction), None)
