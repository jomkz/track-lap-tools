"""Shared helpers for the lap tools: load .xrk, find the track, local x/y, a reference lap path,
and projecting any lap (either direction) onto it with a per-lap GPS shift correction."""
import contextlib
import io
import math
import os

import numpy as np
from scipy.signal import savgol_filter
from scipy.spatial import cKDTree

from libxrk import aim_xrk

import tracks

G = 9.80665
HERE = os.path.dirname(os.path.abspath(__file__))
GPS_GAP_S = 1.0     # a lap with a longer hole in its GPS samples cannot be placed on the track


def smooth(x, dt, win_s):
    n = max(5, int(round(win_s / dt)) | 1)
    return savgol_filter(x, n, 2)


def to_xy(lat, lon, track):
    """Metres east and north of the track's origin."""
    R = 6371000.0
    return (np.radians(np.asarray(lon) - track["lon0"]) * R * math.cos(math.radians(track["lat0"])),
            np.radians(np.asarray(lat) - track["lat0"]) * R)


def read(path):
    """One session's channels. It has no x/y until place() puts it on a track."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        log = aim_xrk(path)
    ch = log.channels
    col = lambda n, i: np.array(ch[n].column(i).to_pylist(), float)
    t = col("GPS Speed", 0) / 1000.0
    s = {"name": (lambda b: b[b.rfind("a_"):] if "a_" in b else b)(os.path.splitext(os.path.basename(path))[0]),
         "t": t, "v": col("GPS Speed", 1), "lat": col("GPS Latitude", 1), "lon": col("GPS Longitude", 1),
         "glat": col("GPS_LateralAcc", 1), "laps": log.laps.to_pylist(),
         "vehicle": log.metadata.get("Vehicle"), "date": log.metadata.get("Log Date"),
         "venue": log.metadata.get("Venue") or ""}
    dt = float(np.median(np.diff(t)))
    s["dt"] = dt
    s["vs"] = smooth(s["v"], dt, 0.4)
    s["ax"] = smooth(np.gradient(s["vs"], t) / G, dt, 0.4)
    s["gl"] = smooth(s["glat"], dt, 0.8)
    return s


def place(sessions, track=None):
    """Put sessions from one circuit on one map: find the track from where their GPS points are
    (a known track from tracks/, else an unnamed one centred on the data) and set x/y from its
    origin. Sessions compared with each other must be placed together."""
    if track is None:
        lat = float(np.nanmedian(np.concatenate([s["lat"] for s in sessions])))
        lon = float(np.nanmedian(np.concatenate([s["lon"] for s in sessions])))
        venues = [s["venue"] for s in sessions if s["venue"]]
        track = tracks.find(lat, lon, max(set(venues), key=venues.count) if venues else "")
    for s in sessions:
        s["track"] = track
        s["x"], s["y"] = to_xy(s["lat"], s["lon"], track)
    return track


def load_day(files):
    """Sessions from one circuit, placed together. Returns (sessions, track)."""
    sessions = [read(f) for f in files]
    return sessions, place(sessions)


def load(path):
    """One session placed on its own (enough for its direction and track)."""
    return load_day([path])[0][0]


def direction(x, y):
    return "CCW" if np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y) > 0 else "CW"


def gps_gap(t, start, end):
    """Longest stretch of a lap (seconds) with no GPS sample; t = the lap's sample times."""
    return float(np.max(np.diff(np.concatenate([[start], t, [end]]))))


def full_laps(sessions, keep_gaps=False):
    """Every logger 'full' lap (AiM's out and pit-in laps are never 'full'). A lap whose GPS
    samples have a hole longer than GPS_GAP_S (the logger dropped them) is left out, because its
    path and its times along the track are not known there; keep_gaps=True keeps those too."""
    out = []
    for s in sessions:
        for l in s["laps"]:
            if l["lap_type"] != "full":
                continue
            t0, t1 = l["start_time"] / 1000, l["end_time"] / 1000
            i = np.where((s["t"] >= t0) & (s["t"] <= t1))[0]
            gap = gps_gap(s["t"][i], t0, t1)
            if (gap > GPS_GAP_S and not keep_gaps) or len(i) < 10:
                continue
            out.append({"s": s, "num": l["num"], "time": t1 - t0, "i": i, "gap": gap,
                        "dir": direction(s["x"][i], s["y"][i])})
    return out


class Ref:
    """A reference lap path; distance 0 at the timing line, in the reference lap's direction."""

    def __init__(self, lap):
        s, i = lap["s"], lap["i"]
        self.track = s["track"]
        self.x, self.y = s["x"][i], s["y"][i]
        self.d = np.concatenate([[0], np.cumsum(np.hypot(np.diff(self.x), np.diff(self.y)))])
        self.L = self.d[-1] + math.hypot(self.x[0] - self.x[-1], self.y[0] - self.y[-1])
        self.tree = cKDTree(np.c_[self.x, self.y])
        self.lap = lap

    def dist_of(self, lat, lon):
        x, y = to_xy(lat, lon, self.track)
        return self.d[self.tree.query(np.c_[np.atleast_1d(x), np.atleast_1d(y)])[1]]

    def project(self, lap):
        """Distance along the reference and off-line metres for every sample of a lap, after
        shifting the lap's GPS by the translation that best fits the reference line."""
        s, i = lap["s"], lap["i"]
        px, py = s["x"][i], s["y"][i]
        sh = np.zeros(2)
        for _ in range(3):
            _, j = self.tree.query(np.c_[px + sh[0], py + sh[1]])
            sh += np.median(np.c_[self.x[j] - px - sh[0], self.y[j] - py - sh[1]], axis=0)
        off, j = self.tree.query(np.c_[px + sh[0], py + sh[1]])
        return self.d[j], off, float(np.hypot(*sh))


def turns(ref, path=None, out_dir=None):
    """Turns with their distance on this reference. From, in order: the given table, the track's
    own turn table (official numbers), or the corners the ideal-lap step found for this day
    (out_dir/corners.csv, numbered in riding order). [] when there is none of these."""
    rows = tracks.read_turns(path) if path else tracks.turn_table(ref.track)
    if not rows and out_dir:
        rows = tracks.read_turns(os.path.join(out_dir, "corners.csv"))
    return [{**r, "d": float(ref.dist_of(r["lat"], r["lon"])[0])} for r in rows]


NO_TURNS = ("no turn positions for this track: run xrk_ideal_lap.py into the same folder first "
            "(it writes corners.csv), or pass --turns FILE.csv")


def on_grid(ref, lap, grid, key):
    """A lap channel resampled onto reference distances (only the on-line samples)."""
    d, off, _ = ref.project(lap)
    s, i = lap["s"], lap["i"]
    m = off < 12
    o = np.argsort(d[m])
    return np.interp(grid, d[m][o], s[key][i][m][o])
