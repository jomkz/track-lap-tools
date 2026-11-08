"""Shared helpers for the Harris Hill lap tools: load .xrk, local x/y, a reference lap path,
and projecting any lap (either direction) onto it with a per-lap GPS shift correction."""
import contextlib
import csv
import io
import math
import os

import numpy as np
from scipy.signal import savgol_filter
from scipy.spatial import cKDTree

from libxrk import aim_xrk

G = 9.80665
HERE = os.path.dirname(os.path.abspath(__file__))
TURNS_CSV = os.path.join(HERE, "harris_hill_turns.csv")
LAT0, LON0 = 29.9180, -97.8720


def smooth(x, dt, win_s):
    n = max(5, int(round(win_s / dt)) | 1)
    return savgol_filter(x, n, 2)


def to_xy(lat, lon):
    R = 6371000.0
    return (np.radians(np.asarray(lon) - LON0) * R * math.cos(math.radians(LAT0)),
            np.radians(np.asarray(lat) - LAT0) * R)


def load(path):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        log = aim_xrk(path)
    ch = log.channels
    col = lambda n, i: np.array(ch[n].column(i).to_pylist(), float)
    t = col("GPS Speed", 0) / 1000.0
    s = {"name": (lambda b: b[b.rfind("a_"):] if "a_" in b else b)(os.path.splitext(os.path.basename(path))[0]),
         "t": t, "v": col("GPS Speed", 1), "lat": col("GPS Latitude", 1), "lon": col("GPS Longitude", 1),
         "glat": col("GPS_LateralAcc", 1), "laps": log.laps.to_pylist(),
         "vehicle": log.metadata.get("Vehicle"), "date": log.metadata.get("Log Date")}
    dt = float(np.median(np.diff(t)))
    s["dt"] = dt
    s["x"], s["y"] = to_xy(s["lat"], s["lon"])
    s["vs"] = smooth(s["v"], dt, 0.4)
    s["ax"] = smooth(np.gradient(s["vs"], t) / G, dt, 0.4)
    s["gl"] = smooth(s["glat"], dt, 0.8)
    return s


def direction(x, y):
    return "CCW" if np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y) > 0 else "CW"


def full_laps(sessions):
    """Every logger 'full' lap (AiM's out and pit-in laps are never 'full')."""
    out = []
    for s in sessions:
        for l in s["laps"]:
            if l["lap_type"] != "full":
                continue
            i = np.where((s["t"] >= l["start_time"] / 1000) & (s["t"] <= l["end_time"] / 1000))[0]
            out.append({"s": s, "num": l["num"], "time": (l["end_time"] - l["start_time"]) / 1000, "i": i,
                        "dir": direction(s["x"][i], s["y"][i])})
    return out


class Ref:
    """A reference lap path; distance 0 at the timing line, in the reference lap's direction."""

    def __init__(self, lap):
        s, i = lap["s"], lap["i"]
        self.x, self.y = s["x"][i], s["y"][i]
        self.d = np.concatenate([[0], np.cumsum(np.hypot(np.diff(self.x), np.diff(self.y)))])
        self.L = self.d[-1] + math.hypot(self.x[0] - self.x[-1], self.y[0] - self.y[-1])
        self.tree = cKDTree(np.c_[self.x, self.y])
        self.lap = lap

    def dist_of(self, lat, lon):
        x, y = to_xy(lat, lon)
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


def turns(ref):
    """Official turn numbers (harris_hill_turns.csv) with their distance on this reference."""
    out = []
    for r in csv.DictReader(open(TURNS_CSV)):
        out.append({"turn": int(r["turn"]), "name": r["name"],
                    "label": f"T{r['turn']}" + (f" {r['name']}" if r["name"] else ""),
                    "d": float(ref.dist_of(float(r["lat"]), float(r["lon"]))[0])})
    return out


def on_grid(ref, lap, grid, key):
    """A lap channel resampled onto reference distances (only the on-line samples)."""
    d, off, _ = ref.project(lap)
    s, i = lap["s"], lap["i"]
    m = off < 12
    o = np.argsort(d[m])
    return np.interp(grid, d[m][o], s[key][i][m][o])
