#!/usr/bin/env python3
"""Ideal lap and corner-by-corner time loss from AiM .xrk files (Solo DL, GPS + IMU).

Usage:
    python3 -I xrk_ideal_lap.py OUT_DIR FILE.xrk [FILE.xrk ...] [--ref SESSION:LAP] [--turns FILE.csv]

All files should be one bike on one day at one track layout. Only laps run in the same direction
as the reference lap are compared (some tracks run both ways). The track is found from where the
GPS points are (scripts/tracks.py); any circuit works, with or without a folder in tracks/.

Method
    laps used    : logger "full" laps (AiM's own out and pit-in laps are never "full", which
                   covers the in-lap rule; --drop-last-full also drops each session's last
                   full lap), minus laps slower than 103% of the
                   day's best, minus laps whose path strays > MAX_OFFLINE_M from the reference,
                   minus laps the GPS did not fully record: a hole of more than GPS_GAP_S in the
                   samples, or section times that do not add up to the lap time within CLOSURE_S.
                   Such a lap keeps its lap time in the lap table, but its times along the track
                   are not known, so it can be neither the best lap here nor part of the ideal.
    reference    : the fastest used lap (or --ref, when its GPS is steadier). Its GPS path
                   defines distance 0..L; "best lap" in the outputs is always the fastest used lap.
                   Every other lap's GPS points are projected onto that path, so "distance"
                   means the same spot on track for every lap.
    corners      : peaks of smoothed lateral g on the reference lap (>= CORNER_G). Named with the
                   track's official turn numbers when it has a turn table (tracks/<id>/turns.csv,
                   or --turns): each corner takes the nearest listed turn within TURN_MATCH_M, so
                   the numbers stay the same in both directions. A corner with no match, and every
                   corner at a track with no table, is numbered T1, T2... in riding order from the
                   timing line. A listed turn too gentle to show as a peak (Harris Hill's turn 3
                   kink) is simply not found.
    sections     : one per corner, from the top-speed point before it to the top-speed point
                   after it (brake zone + corner + drive out). The front straight is split by
                   the timing line into a piece after the line and a piece before it.
    ideal lap    : sum over sections of the fastest time any used lap did in that section.
    GPS drift    : each lap's path is shifted (translation only) onto the reference path first,
                   since the GPS position wanders a few metres between sessions.
    phases       : longitudinal g from the derivative of GPS speed (smoothed ~0.4 s).
                   braking  : long g < -BRAKE_G
                   throttle : long g > +THROTTLE_G sustained THROTTLE_HOLD_S
                   neutral  : in between (coasting, or holding a steady throttle mid-corner).
                   This is inferred from speed only; there is no throttle or brake sensor.
                   Hills shift it a little (uphill makes coasting read as light braking).
Outputs (OUT_DIR): sections.csv, lap_sections.csv, corners.csv, corner_phases.csv,
                   corner_phases_all_laps.csv, summary.md, track_map.png, speed_trace.png,
                   viewer.json (the same numbers for the pit lap viewer, plus what its other
                   views need: see scripts/lap_views.py)
    delta trace  : for the viewer. Each lap's clock (seconds since the timing line) at every
                   DELTA_STEP_M along the reference path, minus the best lap's clock at the same
                   spot: positive = behind the best lap there. Given for the ideal lap (each
                   section at the pace of the lap that was fastest through it) and for every lap
                   in the same direction on the same bike that the GPS recorded fully and that
                   stayed on the reference line, slower than 103% or not.
"""
import argparse
import contextlib
import csv
import io
import math
import os
import sys

import numpy as np
from scipy.signal import find_peaks, savgol_filter

from libxrk import aim_xrk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lap_views  # noqa: E402
import tracks  # noqa: E402

SLOW_FACTOR = 1.03
MAX_OFFLINE_M = 12.0
CORNER_G = 0.45
BRAKE_G = 0.15
HARD_BRAKE_G = 0.30
MERGE_M = 25.0
TURN_MATCH_M = 120.0
SPLIT_MIN_M = 30.0
THROTTLE_G = 0.05
THROTTLE_HOLD_S = 0.4
GPS_GAP_S = 1.0
CLOSURE_S = 0.1
G = 9.80665


def fmt(sec):
    m, s = divmod(sec, 60)
    return f"{int(m)}:{s:06.3f}"


def load(path):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        log = aim_xrk(path)
    ch = log.channels
    t = np.array(ch["GPS Speed"].column(0).to_pylist(), float) / 1000.0
    v = np.array(ch["GPS Speed"].column(1).to_pylist(), float)
    lat = np.array(ch["GPS Latitude"].column(1).to_pylist(), float)
    lon = np.array(ch["GPS Longitude"].column(1).to_pylist(), float)
    glat = np.array(ch["GPS_LateralAcc"].column(1).to_pylist(), float)
    name = os.path.splitext(os.path.basename(path))[0]
    name = name[name.rfind("a_"):] if "a_" in name else name
    return {"name": name, "t": t, "v": v, "lat": lat, "lon": lon, "glat": glat,
            "laps": log.laps.to_pylist(), "vehicle": log.metadata.get("Vehicle"),
            "date": log.metadata.get("Log Date"), "venue": log.metadata.get("Venue") or ""}


def smooth(x, dt, win_s):
    n = max(5, int(round(win_s / dt)) | 1)
    return savgol_filter(x, n, 2)


def to_xy(lat, lon, lat0, lon0):
    R = 6371000.0
    x = np.radians(lon - lon0) * R * math.cos(math.radians(lat0))
    y = np.radians(lat - lat0) * R
    return x, y


def shoelace_dir(x, y):
    a = np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y)
    return "CCW" if a > 0 else "CW"


def lap_slice(s, lap, pad=0.0):
    t0, t1 = lap["start_time"] / 1000 - pad, lap["end_time"] / 1000 + pad
    return np.where((s["t"] >= t0) & (s["t"] <= t1))[0]


def project(px, py, rx, ry, rd, L):
    """Unwrapped reference distance for each point (sequential window search, projected onto
    the reference path's segments so progress is continuous, not stepped)."""
    n = len(rx)
    out = np.empty(len(px))
    off = np.empty(len(px))
    idx = int(np.argmin((rx - px[0]) ** 2 + (ry - py[0]) ** 2))
    wraps = 0
    seglen = np.hypot(np.roll(rx, -1) - rx, np.roll(ry, -1) - ry)
    for k in range(len(px)):
        cand = np.arange(idx - 15, idx + 60)
        ci = cand % n
        d2 = (rx[ci] - px[k]) ** 2 + (ry[ci] - py[k]) ** 2
        j = int(np.argmin(d2))
        new = int(cand[j])
        if new >= n:
            new -= n
            wraps += 1
        elif new < 0:
            new += n
            wraps -= 1
        idx = new
        best = (math.sqrt(d2[j]), rd[idx] + wraps * L)
        for a_i in (idx - 1, idx):
            a0, a1 = a_i % n, (a_i + 1) % n
            ex, ey = rx[a1] - rx[a0], ry[a1] - ry[a0]
            ll = ex * ex + ey * ey
            if ll <= 0:
                continue
            u = ((px[k] - rx[a0]) * ex + (py[k] - ry[a0]) * ey) / ll
            if 0 <= u <= 1:
                dist = math.hypot(px[k] - (rx[a0] + u * ex), py[k] - (ry[a0] + u * ey))
                if dist < best[0]:
                    base = rd[a0] + wraps * L - (L if a0 > idx else 0)
                    best = (dist, base + u * seglen[a0])
        off[k], out[k] = best
    return out, off


def in_sec(d, sec, L):
    """Mask of reference distances d (0..L) inside a section, wrapping through the line."""
    if sec["wrap"]:
        return (d >= sec["d0"]) | (d <= sec["d1"])
    return (d >= sec["d0"]) & (d <= sec["d1"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--ref", help="SESSION:LAP to use as reference, e.g. a_0215:7")
    ap.add_argument("--drop-last-full", action="store_true",
                    help="also drop each session's last full lap (old in-lap rule); by default only AiM's own pit-in lap is left out")
    ap.add_argument("--turns", help="CSV of official turn numbers (turn,name,lat,lon) to use instead of the "
                                    "track's own table; corners get the nearest one")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    sessions = [load(p) for p in sorted(a.files)]
    lat0 = np.nanmedian(np.concatenate([s["lat"] for s in sessions]))
    lon0 = np.nanmedian(np.concatenate([s["lon"] for s in sessions]))
    venues = [s["venue"] for s in sessions if s["venue"]]
    track = tracks.find(float(lat0), float(lon0), max(set(venues), key=venues.count) if venues else "")
    for s in sessions:
        s["x"], s["y"] = to_xy(s["lat"], s["lon"], lat0, lon0)
        dt = float(np.median(np.diff(s["t"])))
        s["dt"] = dt
        vs = smooth(s["v"], dt, 0.4)
        s["vs"] = vs
        s["ax"] = np.gradient(vs, s["t"]) / G
        s["ax"] = smooth(s["ax"], dt, 0.4)
        s["gl"] = smooth(s["glat"], dt, 0.8)

    # candidate laps
    laps = []
    for s in sessions:
        full = [l for l in s["laps"] if l["lap_type"] == "full"]
        for i, l in enumerate(full):
            idx = lap_slice(s, l)
            gap = float(np.max(np.diff(np.concatenate([[l["start_time"] / 1000], s["t"][idx], [l["end_time"] / 1000]]))))
            laps.append({"s": s, "lap": l, "num": l["num"], "time": (l["end_time"] - l["start_time"]) / 1000,
                         "dir": shoelace_dir(s["x"][idx], s["y"][idx]) if len(idx) >= 10 else "",
                         "last": False, "gps_gap": gap})
    if a.drop_last_full:
        for s_ in sessions:
            mine = [L for L in laps if L["s"] is s_]
            if mine:
                mine[-1]["last"] = True
    if a.ref:
        sn, ln = a.ref.split(":")
        ref = next(L for L in laps if L["s"]["name"] == sn and L["num"] == int(ln))
    else:
        ref = min((L for L in laps if not L["last"] and L["gps_gap"] <= GPS_GAP_S), key=lambda L: L["time"])
    best = min(L["time"] for L in laps if not L["last"] and L["dir"] == ref["dir"]
               and L["s"]["vehicle"] == ref["s"]["vehicle"] and L["gps_gap"] <= GPS_GAP_S)
    for L in laps:
        L["why_out"] = ""
        if L["gps_gap"] > GPS_GAP_S:
            L["why_out"] = f"GPS dropout of {L['gps_gap']:.0f} s in the log"
        elif L["dir"] != ref["dir"]:
            L["why_out"] = "other direction"
        elif L["s"]["vehicle"] != ref["s"]["vehicle"]:
            L["why_out"] = "other bike"
        elif L["last"]:
            L["why_out"] = "last full lap (--drop-last-full)"
        elif L["time"] > best * SLOW_FACTOR:
            L["why_out"] = f"slower than {int(round((SLOW_FACTOR - 1) * 100))}% off best"

    # reference path
    rs = ref["s"]
    ri = lap_slice(rs, ref["lap"])
    rx, ry = rs["x"][ri], rs["y"][ri]
    seg = np.hypot(np.diff(rx), np.diff(ry))
    rd = np.concatenate([[0], np.cumsum(seg)])
    L_ref = rd[-1] + math.hypot(rx[0] - rx[-1], ry[0] - ry[-1])
    rv, rgl = rs["vs"][ri], rs["gl"][ri]

    # corners on reference lap
    glw = np.concatenate([rgl[-100:], rgl, rgl[:100]])
    pk, _ = find_peaks(np.abs(glw), height=CORNER_G, prominence=0.25, distance=int(2.0 / rs["dt"]))
    pk = [p - 100 for p in pk if 100 <= p < 100 + len(rgl)]
    apex_d = [rd[p] for p in pk]
    apex_side = ["R" if rgl[p] > 0 else "L" for p in pk]   # AiM lateral g: + = right turn
    rax = rs["ax"][ri]
    # boundary between two corners: where he rolls off the throttle for a real brake zone
    # (so braking belongs to the corner it is for); with no brake zone, the straightest point.
    bounds = []
    for k in range(len(pk)):
        p0, p1 = pk[k], pk[(k + 1) % len(pk)]
        seg_i = np.arange(p0, p1) if p1 > p0 else np.concatenate([np.arange(p0, len(rv)), np.arange(0, p1)])
        hard = np.where(rax[seg_i] < -HARD_BRAKE_G)[0]
        if len(hard):
            q = hard[0]
            while q > 0 and rax[seg_i[q]] < 0:
                q -= 1
            gap = (rd[seg_i[q]] - rd[p0]) % L_ref
            if gap < MERGE_M:
                continue   # he is already slowing at this apex: one section for both corners
            bounds.append(int(seg_i[q]))
        else:
            bounds.append(int(seg_i[np.argmin(np.abs(rgl[seg_i]))]))
    bd = sorted(rd[b] for b in bounds)
    # cyclic sections; the one that holds the timing line wraps through it
    order = sorted(range(len(apex_d)), key=lambda i: apex_d[i])
    tname = {ci: f"T{n + 1}" for n, ci in enumerate(order)}
    # official turn numbers: nearest listed turn within TURN_MATCH_M, when the track has a turn table
    known = tracks.read_turns(a.turns or track["turns_csv"])
    if known:
        kx, ky = to_xy(np.array([k["lat"] for k in known]), np.array([k["lon"] for k in known]), lat0, lon0)
        for ci in order:
            dist = np.hypot(kx - rx[pk[ci]], ky - ry[pk[ci]])
            j = int(np.argmin(dist))
            if dist[j] < TURN_MATCH_M:
                tname[ci] = known[j]["label"]
    sections = []
    for k in range(len(bd)):
        d0, d1 = bd[k - 1], bd[k]
        wrap = d1 <= d0
        inside = [i for i, d in enumerate(apex_d) if (d0 <= d < d1) if not wrap] if not wrap else \
                 [i for i, d in enumerate(apex_d) if d >= d0 or d < d1]
        inside.sort(key=lambda i: (apex_d[i] - d0) % L_ref)
        sections.append({"d0": d0, "d1": d1, "wrap": wrap, "corners": inside})
    sections.sort(key=lambda sec: min(apex_d[c] for c in sec["corners"]) if sec["corners"] else 0)
    for k, sec in enumerate(sections):
        sec["name"] = "+".join(tname[c] for c in sec["corners"]) or f"Straight {k + 1}"
        sec["side"] = "/".join(apex_side[c] for c in sec["corners"])
        sec["id"] = k + 1
    corners = sections
    # split each corner section at the reference lap's slowest point: "in" (braking and
    # turn-in) and "out" (drive out to the next brake point)
    sections = []
    for c in corners:
        m = in_sec(rd, c, L_ref)
        cand = np.where(m)[0]
        dm = rd[cand[np.argmin(rv[cand])]]
        c["min_d"] = dm
        span = (c["d1"] - c["d0"]) % L_ref
        if (dm - c["d0"]) % L_ref < SPLIT_MIN_M or (c["d1"] - dm) % L_ref < SPLIT_MIN_M:
            parts = (("", (c["d0"], c["d1"])),)   # flat-out or accelerating corner: no split
        else:
            parts = (("in", (c["d0"], dm)), ("out", (dm, c["d1"])))
        for part, (a0, a1) in parts:
            sections.append({"d0": a0, "d1": a1, "wrap": a1 < a0, "parent": c,
                             "name": f"{c['name']} {part}".strip(), "side": c["side"]})
    for k, sec in enumerate(sections):
        sec["id"] = k + 1
    edges = sorted(set([0.0, L_ref] + [sec["d0"] for sec in sections] + [sec["d1"] for sec in sections]))

    # project every lap and get section times
    rows = []
    for L in laps:
        s = L["s"]
        idx = lap_slice(s, L["lap"], pad=4.0)
        px, py = s["x"][idx].copy(), s["y"][idx].copy()
        # GPS drifts a few metres between sessions; shift each lap onto the reference path
        # (translation only) so the same spot on track gets the same distance
        sh = np.zeros(2)
        if L is not ref:
            for _ in range(6):
                nn = [int(np.argmin((rx - x) ** 2 + (ry - y) ** 2)) for x, y in zip(px[::5], py[::5])]
                dxy = np.array([np.median(rx[nn] - px[::5]), np.median(ry[nn] - py[::5])])
                px += dxy[0]
                py += dxy[1]
                sh += dxy
        L["shift_m"] = float(np.hypot(*sh))
        prog, off = project(px, py, rx, ry, rd, L_ref)
        # make the lap start near 0
        t = s["t"][idx]
        tstart = L["lap"]["start_time"] / 1000
        p_at_start = np.interp(tstart, t, prog)
        prog -= round(p_at_start / L_ref) * L_ref
        # ensure monotonic for interpolation
        mono = np.maximum.accumulate(prog)
        inlap = (t >= tstart) & (t <= L["lap"]["end_time"] / 1000)
        L["max_off"] = float(np.max(off[inlap]))
        if not L["why_out"] and L["max_off"] > MAX_OFFLINE_M:
            L["why_out"] = f"off the reference line by {L['max_off']:.0f} m"
        L["prog"], L["t"], L["idx"] = mono, t, idx
        tc = {e: float(np.interp(e, mono, t)) for e in edges}
        L["sec_t"] = np.array([(tc[sec["d1"]] - tc[sec["d0"]]) if not sec["wrap"] else
                               (tc[sec["d1"]] - tc[0.0]) + (tc[L_ref] - tc[sec["d0"]]) for sec in sections])
        L["closure"] = float(L["sec_t"].sum() - L["time"])
        if not L["why_out"] and abs(L["closure"]) > CLOSURE_S:
            L["why_out"] = f"section times do not add up to the lap time (off by {L['closure']:+.1f} s)"
        rows.append(L)
    used = [L for L in rows if not L["why_out"]]
    ideal_t = np.min(np.array([L["sec_t"] for L in used]), axis=0)
    ideal_from = [used[int(np.argmin([L["sec_t"][k] for L in used]))] for k in range(len(sections))]
    ideal = float(ideal_t.sum())
    fast = min(used, key=lambda L: L["time"])  # his best lap; ref only sets the track geometry
    ref_sec = fast["sec_t"]
    lost = ref_sec - ideal_t

    # phases per lap per section
    def phases(L, sec):
        s = L["s"]
        idx = L["idx"]
        d = L["prog"]
        if sec["wrap"]:
            m = (d >= sec["d0"] - L_ref) & (d <= sec["d1"])
        else:
            m = (d >= sec["d0"]) & (d <= sec["d1"])
        if m.sum() < 10:
            return None
        ii = idx[m]
        dd, vv, ax, tt = d[m], s["vs"][ii] * 3.6, s["ax"][ii], s["t"][ii]
        a_i = int(np.argmin(vv))
        res = {"min_kph": float(vv[a_i]), "min_at_m": float(dd[a_i]),
               "entry_kph": float(vv[0]), "exit_kph": float(vv[-1])}
        # main brake zone before the slowest point: hard braking (< -HARD_BRAKE_G), then back to
        # where the brakes come on (< -BRAKE_G) and to where the throttle closed (< +THROTTLE_G)
        hard = np.where(ax[:a_i + 1] < -HARD_BRAKE_G)[0]
        rel = 0
        if len(hard):
            # the last hard block before the apex
            h1 = hard[-1]
            h0 = h1
            while h0 > 0 and ax[h0 - 1] < -HARD_BRAKE_G:
                h0 -= 1
            j = h0
            while j > 0 and ax[j - 1] < -BRAKE_G:
                j -= 1
            k0 = j
            while k0 > 0 and ax[k0 - 1] < THROTTLE_G:
                k0 -= 1
            rel = h1
            while rel < a_i and ax[rel + 1] < -BRAKE_G:
                rel += 1
            res["off_throttle_m"] = float(dd[k0])
            res["brake_at_m"] = float(dd[j])
            res["peak_brake_g"] = float(-ax[j:a_i + 1].min())
            res["coast_before_brake_s"] = float(tt[j] - tt[k0])
            res["brake_release_m"] = float(dd[rel])
        else:
            for kk in ("off_throttle_m", "brake_at_m", "coast_before_brake_s", "brake_release_m"):
                res[kk] = float("nan")
            res["peak_brake_g"] = float(max(0, -ax[:a_i + 1].min()))
        hold = max(2, int(THROTTLE_HOLD_S / s["dt"]))
        pick = None
        for q in range(max(a_i, rel), len(ax) - hold):
            if np.all(ax[q:q + hold] > THROTTLE_G):
                pick = q
                break
        if pick is not None:
            res["throttle_at_m"] = float(dd[pick])
            res["throttle_after_min_m"] = float(dd[pick] - dd[a_i])
            res["neutral_mid_s"] = float(tt[pick] - tt[rel]) if rel < pick else 0.0
        else:
            res["throttle_at_m"] = res["throttle_after_min_m"] = res["neutral_mid_s"] = float("nan")
        return res

    # outputs: sections.csv
    with open(os.path.join(a.out, "sections.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["section", "name", "side", "start_m", "end_m", "best_lap_s", "ideal_s", "lost_s",
                    "ideal_from"])
        for k, sec in enumerate(sections):
            src = ideal_from[k]
            w.writerow([sec["id"], sec["name"], sec["side"], round(sec["d0"]), round(sec["d1"]),
                        round(ref_sec[k], 3), round(ideal_t[k], 3), round(lost[k], 3),
                        f"{src['s']['name']} lap {src['num']}"])
    # corner list with apex position, so the same bend can be matched across directions
    rlat, rlon = rs["lat"][ri], rs["lon"][ri]
    with open(os.path.join(a.out, "corners.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["corner", "turn", "apex_m", "apex_lat", "apex_lon", "peak_lat_g", "min_kph_best_lap"])
        for ci in order:
            p = pk[ci]
            w.writerow([tname[ci], apex_side[ci], round(rd[p]), round(float(rlat[p]), 6), round(float(rlon[p]), 6),
                        round(abs(float(rgl[p])), 2), round(float(rv[p]) * 3.6, 1)])
    with open(os.path.join(a.out, "lap_sections.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["session", "lap", "lap_s", "direction", "used", "why_out", "max_offline_m", "gps_shift_m"] +
                   [f"s{sec['id']}_{sec['name']}" for sec in sections])
        for L in rows:
            w.writerow([L["s"]["name"], L["num"], round(L["time"], 3), L["dir"], not L["why_out"], L["why_out"],
                        round(L["max_off"], 1), round(L["shift_m"], 1)] + [round(x, 3) for x in L["sec_t"]])
    # corner-level numbers: corner time = its in + out parts
    for c in corners:
        ks = [k for k, sec in enumerate(sections) if sec["parent"] is c]
        c["ks"] = ks
        c["ref_t"] = float(sum(ref_sec[k] for k in ks))
        c["ideal_t"] = float(sum(ideal_t[k] for k in ks))
        c["lost"] = c["ref_t"] - c["ideal_t"]
        ct = [sum(L["sec_t"][k] for k in ks) for L in used]
        c["best_lap"] = used[int(np.argmin(ct))]
        c["best_whole"] = float(min(ct))
        c["lost_whole"] = c["ref_t"] - c["best_whole"]
    ph_rows = []
    for c in corners:
        for tag, L in (("best lap", fast), ("fastest through this corner", c["best_lap"])):
            p = phases(L, c)
            if p:
                ph_rows.append({"corner": c["name"], "side": c["side"], "which": tag,
                                "lap": f"{L['s']['name']} lap {L['num']}",
                                "corner_s": round(float(sum(L["sec_t"][k] for k in c["ks"])), 3),
                                **{kk: round(vv, 2) if isinstance(vv, float) else vv for kk, vv in p.items()}})
    if False:
        for tag, L in ():
            p = None
    all_rows = []
    for c in corners:
        for L in used:
            p = phases(L, c)
            if p:
                all_rows.append({"corner": c["name"], "lap": f"{L['s']['name']} lap {L['num']}",
                                 "is_best_lap": L is fast,
                                 "corner_s": round(float(sum(L["sec_t"][k] for k in c["ks"])), 3),
                                 **{kk: round(vv, 2) if isinstance(vv, float) else vv for kk, vv in p.items()}})
    with open(os.path.join(a.out, "corner_phases_all_laps.csv"), "w", newline="") as f:
        keys = list(dict.fromkeys(k for r in all_rows for k in r))
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(all_rows)
    with open(os.path.join(a.out, "corner_phases.csv"), "w", newline="") as f:
        keys = list(dict.fromkeys(k for r in ph_rows for k in r))
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(ph_rows)

    # summary
    out_reasons = {}
    for L in rows:
        if L["why_out"]:
            out_reasons.setdefault(L["why_out"], []).append(f"{L['s']['name']} L{L['num']}")
    rank = sorted(range(len(sections)), key=lambda k: -lost[k])
    crank = sorted(corners, key=lambda c: -c["lost_whole"])
    lines = [f"# Ideal lap, {ref['s']['date']} ({track['name']}, {ref['s']['vehicle']}, {ref['dir']})", "",
             f"- Best lap: {fast['s']['name']} lap {fast['num']}, {fmt(fast['time'])}",
             *([f"- Track geometry from: {ref['s']['name']} lap {ref['num']} (steadier GPS)"] if ref is not fast else []),
             f"- Ideal lap (best section times, {len(used)} laps): {fmt(ideal)}",
             f"- Gap: {fast['time'] - ideal:.2f} s",
             f"- Ideal from whole corners only (each corner done in one go on one lap): "
             f"{fmt(sum(c['best_whole'] for c in corners))}", f"- Lap length on the GPS path: {L_ref:.0f} m",
             f"- Sum of best-lap sections: {ref_sec.sum():.3f} s (lap timer {fast['time']:.3f} s)", "",
             "## Laps left out", ""]
    lines += [f"- {k}: {', '.join(v)}" for k, v in out_reasons.items()]
    lines += ["", "## Corners, most time lost to the fastest single run through it", "",
              "Corner = from where he closes the throttle for its brake zone to the next one.", "",
              "| Corner | Turns | Best lap | Ideal (in+out) | Lost | In / out | Fastest whole corner | Lost to it |",
              "|---|---|---|---|---|---|---|---|"]
    for c in crank:
        io_ = " / ".join(f"{lost[k]:.2f}" for k in c["ks"]) if len(c["ks"]) == 2 else "no split"
        lines.append(f"| {c['name']} | {c['side']} | {c['ref_t']:.2f} | {c['ideal_t']:.2f} | {c['lost']:.2f} | "
                     f"{io_} | {c['best_lap']['s']['name']} L{c['best_lap']['num']} "
                     f"({c['best_whole']:.2f}) | {c['lost_whole']:.2f} |")
    lines += ["", "## Half-sections, most time lost first", "",
              "| # | Corner | Best lap | Ideal | Lost | Ideal from |", "|---|---|---|---|---|---|"]
    for k in rank:
        sec = sections[k]
        src = ideal_from[k]
        lines.append(f"| {sec['id']} | {sec['name']} {sec['side']} | {ref_sec[k]:.2f} | {ideal_t[k]:.2f} | "
                     f"{lost[k]:.2f} | {src['s']['name']} L{src['num']} |")
    open(os.path.join(a.out, "summary.md"), "w").write("\n".join(lines) + "\n")

    # charts
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    from matplotlib import cm, colors

    ink, muted, grid, base, surf = "#0b0b0b", "#52514e", "#e1e0d9", "#c3c2b7", "#fcfcfb"
    s1, s2 = "#2a78d6", "#eb6834"
    plt.rcParams.update({"font.size": 11, "text.color": ink, "axes.labelcolor": muted,
                         "xtick.color": muted, "ytick.color": muted, "axes.edgecolor": grid})
    # track map
    sec_of = np.zeros(len(rd), int)
    for k, sec in enumerate(sections):
        sec_of[in_sec(rd, sec, L_ref)] = k
    vals = lost[sec_of]
    pts = np.column_stack([rx, ry])
    segs = np.stack([pts[:-1], pts[1:]], axis=1)
    norm = colors.Normalize(0, max(0.3, float(lost.max())))
    cmap = colors.LinearSegmentedColormap.from_list("loss", ["#86b6ef", "#3987e5", "#1c5cab", "#0d366b"])
    fig, axm = plt.subplots(figsize=(8, 8), facecolor=surf)
    axm.set_facecolor(surf)
    axm.plot(rx, ry, color=base, lw=11, solid_capstyle="round", zorder=1)
    lc = LineCollection(segs, cmap=cmap, norm=norm, linewidths=7, capstyle="round", zorder=2)
    lc.set_array(vals[:-1])
    axm.add_collection(lc)
    cx, cy = np.mean(rx), np.mean(ry)
    for ci, p in enumerate(pk):
        dx, dy = rx[p] - cx, ry[p] - cy
        n = math.hypot(dx, dy) or 1
        par = next(c for c in corners if ci in c["corners"])
        top = par in sorted(corners, key=lambda c: -c["lost"])[:3] and ci == par["corners"][-1]
        short = tname[ci].split()[0]
        lab = f"{short}\n{par['lost']:.2f} s" if top else short
        axm.annotate(lab, (rx[p], ry[p]), xytext=(rx[p] + 42 * dx / n, ry[p] + 42 * dy / n),
                     ha="center", va="center", fontsize=10, color=ink, fontweight="bold")
    # start line and direction arrow
    axm.plot([rx[0]], [ry[0]], marker="s", color=ink, ms=8, zorder=3)
    axm.annotate("start/finish", (rx[0], ry[0]), xytext=(10, -16), textcoords="offset points",
                 fontsize=9, color=muted)
    a2 = min(25, len(rx) - 1)
    axm.annotate("", xy=(rx[a2], ry[a2]), xytext=(rx[0], ry[0]),
                 arrowprops=dict(arrowstyle="-|>", color=ink, lw=1.5), zorder=4)
    axm.set_aspect("equal")
    axm.axis("off")
    cb = fig.colorbar(cm.ScalarMappable(norm=norm, cmap=cmap), ax=axm, shrink=0.55, pad=0.02)
    cb.set_label("Seconds lost vs your best section", color=muted)
    cb.outline.set_visible(False)
    axm.set_title(f"Where the {fmt(fast['time'])} lap lost time to the {fmt(ideal)} ideal "
                  f"({ref['dir']}, {ref['s']['date']})", fontsize=12, loc="left")
    fig.tight_layout()
    fig.savefig(os.path.join(a.out, "track_map.png"), dpi=150, facecolor=surf)
    plt.close(fig)

    # speed trace: best lap vs composite of best-section laps
    def trace(L):
        s = L["s"]
        return L["prog"], s["vs"][L["idx"]] * 3.6
    grid_d = np.linspace(0, L_ref, 1500)
    bd_, bv_ = trace(fast)
    best_v = np.interp(grid_d, bd_, bv_)
    comp_v = np.empty_like(grid_d)
    for k, sec in enumerate(sections):
        m = in_sec(grid_d, sec, L_ref)
        d_, v_ = trace(ideal_from[k])
        comp_v[m] = np.interp(grid_d[m], d_, v_)
    fig, ax1 = plt.subplots(figsize=(12, 4.2), facecolor=surf)
    ax1.set_facecolor(surf)
    for k, sec in enumerate(sections):
        if k in rank[:3]:
            spans = [(sec["d0"], L_ref), (0, sec["d1"])] if sec["wrap"] else [(sec["d0"], sec["d1"])]
            for x0, x1 in spans:
                ax1.axvspan(x0, x1, color="#f0efec", lw=0, zorder=0)
    ax1.plot(grid_d, best_v, color=s1, lw=2, label=f"Best lap {fmt(fast['time'])}")
    ax1.plot(grid_d, comp_v, color=s2, lw=2, label=f"Ideal {fmt(ideal)} (your best in each section)")
    for ci, p in enumerate(pk):
        ax1.text(rd[p], 4 + float(np.interp(rd[p], grid_d, np.minimum(best_v, comp_v))) - 14, tname[ci].split()[0],
                 ha="center", fontsize=9, color=ink)
    ax1.set_xlim(0, L_ref)
    ax1.set_xlabel("Distance from start/finish (m)")
    ax1.set_ylabel("Speed (km/h)")
    for sp in ("top", "right"):
        ax1.spines[sp].set_visible(False)
    ax1.grid(axis="y", color=grid, lw=0.6)
    ax1.spines["bottom"].set_color(base)
    ax1.spines["left"].set_visible(False)
    ax1.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.18), ncol=2)
    ax1.set_title("Speed around the lap: best lap vs ideal (shaded: the 3 sections that lose most)",
                  fontsize=12, loc="left")
    fig.tight_layout()
    fig.savefig(os.path.join(a.out, "speed_trace.png"), dpi=150, facecolor=surf)
    plt.close(fig)

    # laps whose times along the track can be trusted, fast enough for the ideal or not
    def comparable(L):
        return (L["dir"] == ref["dir"] and L["s"]["vehicle"] == ref["s"]["vehicle"] and not L["last"]
                and L["gps_gap"] <= GPS_GAP_S and L["max_off"] <= MAX_OFFLINE_M and abs(L["closure"]) <= CLOSURE_S)

    # time delta trace: every comparable lap's clock against the best lap's, along the reference path
    curve = lambda L: (L["prog"], L["t"])
    dgrid = lap_views.lap_grid(L_ref)
    best_clock = lap_views.clock_at(curve(fast), dgrid)
    pieces = lap_views.pieces_in_order([(sec["d0"], sec["d1"], sec["wrap"], k) for k, sec in enumerate(sections)], L_ref)
    ideal_clock, ideal_total = lap_views.composite_clock(dgrid, pieces, {k: curve(L) for k, L in enumerate(ideal_from)})
    if abs(ideal_total - ideal) > 0.01:
        raise SystemExit(f"delta trace: the stitched ideal lap is {ideal_total:.3f} s but the ideal is {ideal:.3f} s")
    comp = [L for L in rows if comparable(L)]
    session_best = {id(min((L for L in comp if L["s"] is s_), key=lambda L: L["time"]))
                    for s_ in sessions if any(L["s"] is s_ for L in comp)}
    delta = {"step_m": lap_views.DELTA_STEP_M, "d": [round(float(x), 1) for x in dgrid],
             "ideal_cs": lap_views.delta_cs(ideal_clock, best_clock),
             "laps": [{"session": L["s"]["name"], "lap": L["num"], "time_s": round(L["time"], 3),
                       "used": not L["why_out"], "session_best": id(L) in session_best,
                       "cs": lap_views.delta_cs(lap_views.clock_at(curve(L), dgrid), best_clock)} for L in comp]}

    # viewer.json: the same map and trace numbers as the PNGs, for the pit lap viewer (viewer/)
    import json
    step = max(1, len(rd) // 600)
    r3 = lambda arr: [round(float(x), 1) for x in arr]
    json.dump({
        "date": ref["s"]["date"], "vehicle": ref["s"]["vehicle"], "direction": ref["dir"],
        "track": {"id": track["id"], "name": track["name"], "known": track["known"],
                  "turn_numbers": "table" if known else "riding order", "turns_note": track["turns_note"]},
        "best_lap": {"session": fast["s"]["name"], "lap": fast["num"], "time_s": round(fast["time"], 3)},
        "geometry_from": {"session": ref["s"]["name"], "lap": ref["num"]},
        "ideal_s": round(ideal, 3), "ideal_whole_corners_s": round(sum(c["best_whole"] for c in corners), 3),
        "laps_used": len(used), "lap_length_m": round(L_ref, 1),
        "path": {"d": r3(rd[::step]), "x": r3(rx[::step]), "y": r3(ry[::step]),
                 "lat": [round(float(v), 6) for v in rlat[::step]], "lon": [round(float(v), 6) for v in rlon[::step]]},
        "sections": [{"id": sec["id"], "name": sec["name"], "side": sec["side"], "d0": round(sec["d0"], 1),
                      "d1": round(sec["d1"], 1), "wrap": bool(sec["wrap"]), "corner": sec["parent"]["name"],
                      "best_s": round(float(ref_sec[k]), 3), "ideal_s": round(float(ideal_t[k]), 3),
                      "lost_s": round(float(lost[k]), 3),
                      "ideal_from": f"{ideal_from[k]['s']['name']} lap {ideal_from[k]['num']}"}
                     for k, sec in enumerate(sections)],
        "corners": [{"name": c["name"], "side": c["side"], "d0": round(c["d0"], 1), "d1": round(c["d1"], 1),
                     "wrap": bool(c["wrap"]), "min_d": round(float(c["min_d"]), 1),
                     "best_s": round(c["ref_t"], 3), "ideal_s": round(c["ideal_t"], 3), "lost_s": round(c["lost"], 3),
                     "best_whole_s": round(c["best_whole"], 3), "lost_whole_s": round(c["lost_whole"], 3),
                     "fastest_whole": f"{c['best_lap']['s']['name']} lap {c['best_lap']['num']}"} for c in corners],
        "apexes": [{"label": tname[ci], "d": round(float(rd[p]), 1), "x": round(float(rx[p]), 1),
                    "y": round(float(ry[p]), 1), "side": apex_side[ci]} for ci, p in enumerate(pk)],
        "trace": {"d": r3(grid_d[::2]), "best_kph": r3(best_v[::2]), "ideal_kph": r3(comp_v[::2])},
        "delta": delta,
    }, open(os.path.join(a.out, "viewer.json"), "w"), separators=(",", ":"))

    print("\n".join(lines))


if __name__ == "__main__":
    main()
