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
    stint        : for the viewer, per session. Its full laps in this direction with the minute on
                   track each ended, and whether each is clean (the lap table's rule, lap_rules.py).
                   trend: a robust line through the clean laps (lap_views.robust_trend), given
                   only from MIN_TREND_LAPS clean laps up. late_early: over the clean laps whose
                   section times can be trusted, the mean of the last third minus the mean of the
                   first third in every section (the lap table's fade, split by section), with
                   totals for the brake-in parts, the drive-out parts and the unsplit corners.
    grip         : for the viewer, over the laps used for the ideal. Cornering g is GPS lateral g with
                   spikes taken out, then smoothed; acceleration g is the change in GPS speed.
                   lean: atan(cornering g) in degrees, the lean of bike and rider together on a
                   flat road. Per turn, the deepest lean held for HOLD_S within LEAN_WIN_M of the
                   apex: on the best lap, the most on any used lap, and the median per session.
                   grip circle: the best lap's samples (cornering g against acceleration g) and
                   the outline of the grip reached through the day by direction: each lap's peak
                   in a direction is the level it held for ENV_HOLD_S there, and the outline is
                   the ENV_LAP_PCT percentile of the laps' peaks, so a glitch or one odd lap does
                   not set it and it does not grow just because more laps were ridden. Per corner on
                   the best lap: the share of its time within NEAR_LIMIT of the outline, and the
                   lowest share of the outline used between the hardest braking and the deepest
                   lean (high = braking was traded for lean, low = the brake was released first).
                   gyro: how well the logger's gyro follows the GPS lean rate (r), a cross-check.
                   All of it is an estimate from GPS: hanging off, tire width, banking and
                   crests are not in it.
    engine       : for the viewer, only when the logs carry ECU channels. Engine speed, throttle,
                   brake and gear are found by channel name or by the function the logger gives
                   them (lap_views.ROLE_NAMES). A channel is used only when every session has it
                   and it moves; one that is in the log but flat (not wired, or not sent by the
                   ECU) is reported as flat and never drawn. Throttle and brake are shown as a
                   percentage of the range they covered that day. With no gear channel, gear is
                   worked out from revs and GPS speed when the bike's gearbox ratios are known
                   (bikes/, lap_views.gears_from_ratios), and not shown otherwise: guessing the
                   gearbox from the data gave different gears on different days. Per corner, for the best lap and the
                   fastest run through it: speed, revs and gear at the slowest point, lowest revs,
                   revs at the exit, and with a throttle channel where the throttle comes back on
                   relative to the slowest point and the seconds spent coasting.
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
import bikes  # noqa: E402
import lap_rules  # noqa: E402
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
    try:        # the logger's own gyro, on the GPS clock (a cross-check for the lean estimate)
        gyro = {k: np.interp(t, np.array(ch[k].column(0).to_pylist(), float) / 1000.0,
                             np.array(ch[k].column(1).to_pylist(), float)) for k in ("RollRate", "YawRate", "PitchRate")}
    except (KeyError, IndexError, ValueError):
        gyro = None
    # engine channels from the ECU, when the bike sends any: each on the GPS clock, with whether it carries a signal
    meta = {}
    for n in ch:
        md = {k.decode(): val.decode() for k, val in (ch[n].schema.field(1).metadata or {}).items()}
        meta[n] = {"units": md.get("units", ""), "function": md.get("function", "")}
    eng = {}
    for role, n in lap_views.channel_roles(meta).items():
        if n is None:
            continue
        tc = np.array(ch[n].column(0).to_pylist(), float) / 1000.0
        vc = np.array(ch[n].column(1).to_pylist(), float)
        if len(tc) < 2:
            continue
        on_gps = vc[np.clip(np.searchsorted(tc, t, side="right") - 1, 0, len(vc) - 1)] if role == "gear" else np.interp(t, tc, vc)
        state, why = lap_views.channel_state(vc, role)
        eng[role] = {"name": n, "raw": on_gps, "own_t": tc, "own": vc, "state": state,
                     "note": f"{why} {meta[n]['units']}".strip() if why else ""}
    return {"name": name, "t": t, "v": v, "lat": lat, "lon": lon, "glat": glat, "gyro": gyro, "eng": eng,
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
        s["gld"] = smooth(lap_views.despike(s["glat"]), dt, 0.8)    # for lean and the grip circle

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
            sections.append({"d0": a0, "d1": a1, "wrap": a1 < a0, "parent": c, "part": part or "whole",
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

    # stint view: each session's laps against time on track, a trend that one slow lap cannot tilt,
    # and where the late laps lost time to the early ones, section by section
    stint = []
    for s_ in sessions:
        mine = [L for L in rows if L["s"] is s_ and L["dir"] == ref["dir"] and L["s"]["vehicle"] == ref["s"]["vehicle"]]
        if not mine:
            continue
        clean = lap_rules.clean_flags([L["time"] for L in rows if L["s"] is s_])      # over all its full laps, as the lap table does
        clean = dict(zip((id(L) for L in rows if L["s"] is s_), clean))
        end_min = lambda L: L["lap"]["end_time"] / 60000.0
        cl = [L for L in mine if clean[id(L)]]
        trend = lap_views.robust_trend([end_min(L) for L in cl], [L["time"] for L in cl])
        placed = [L for L in cl if comparable(L)]       # clean laps whose section times can be trusted
        k = lap_rules.third(len(placed))
        diff = lap_views.late_minus_early([L["sec_t"] for L in placed], k)
        stint.append({
            "session": s_["name"],
            "laps": [{"lap": L["num"], "end_min": round(end_min(L), 2), "time_s": round(L["time"], 3),
                      "clean": bool(clean[id(L)]), "placed": bool(comparable(L))} for L in mine],
            "trend": None if trend is None else {"s_per_min": round(trend["s_per_min"], 4),
                                                 "at_0_s": round(trend["at_0_s"], 3), "n": trend["n"]},
            "late_early": None if diff is None else {
                "early": [L["num"] for L in placed[:k]], "late": [L["num"] for L in placed[-k:]],
                "sec_s": [round(float(x), 3) for x in diff], "lap_s": round(float(diff.sum()), 3),
                "in_s": round(float(sum(x for x, sec in zip(diff, sections) if sec["part"] == "in")), 3),
                "out_s": round(float(sum(x for x, sec in zip(diff, sections) if sec["part"] == "out")), 3),
                "whole_s": round(float(sum(x for x, sec in zip(diff, sections) if sec["part"] == "whole")), 3)}})

    # grip view: lean per turn and the grip circle, from the despiked cornering g and the speed-derived
    # acceleration g, over the laps used for the ideal
    def in_lap(L):
        return (L["t"] >= L["lap"]["start_time"] / 1000) & (L["t"] <= L["lap"]["end_time"] / 1000)

    def runs(mask):
        """Index ranges of consecutive True samples."""
        i = np.flatnonzero(mask)
        return np.split(i, np.flatnonzero(np.diff(i) > 1) + 1) if len(i) else []

    apex_ds = sorted(float(rd[p]) for p in pk)

    def turn_lean(L, a):
        """Deepest lean (degrees) held within LEAN_WIN_M of the apex at distance a, and nearer to it
        than to any other apex (so two turns close together each read their own lean), or nan."""
        hold = max(2, int(round(lap_views.HOLD_S / L["s"]["dt"])))
        gap = lambda d: np.abs((d - a + L_ref / 2) % L_ref - L_ref / 2)
        others = [gap(L["prog"] - (o - a)) for o in apex_ds if o != a]
        near = in_lap(L) & (gap(L["prog"]) < lap_views.LEAN_WIN_M)
        for o in others:
            near &= gap(L["prog"]) <= o
        g = np.abs(L["s"]["gld"][L["idx"]])
        peaks = [lap_views.held_peak(g[r], hold) for r in runs(near)]
        peaks = [p for p in peaks if p == p]
        return float(lap_views.lean_deg(max(peaks))) if peaks else float("nan")

    lat_of = lambda L: L["s"]["gld"][L["idx"]]
    lon_of = lambda L: L["s"]["ax"][L["idx"]]
    env = lap_views.envelope([(lat_of(L)[in_lap(L)], lon_of(L)[in_lap(L)]) for L in used],
                             top_n=round(lap_views.ENV_HOLD_S / rs["dt"]))
    grip = None
    if not np.isnan(env).any():
        turns_out = []
        for ci in order:
            a_d = float(rd[pk[ci]])
            per = [(turn_lean(L, a_d), L) for L in used]
            per = [(v, L) for v, L in per if v == v]
            if not per:
                continue
            top = max(per, key=lambda x: x[0])
            mine = turn_lean(fast, a_d)
            by_session = []
            for s_ in sessions:
                vs = [v for v, L in per if L["s"] is s_]
                if vs:
                    by_session.append({"session": s_["name"], "deg": round(float(np.median(vs)), 1), "laps": len(vs)})
            turns_out.append({"label": tname[ci], "side": apex_side[ci], "d": round(a_d, 1),
                              "corner": next(c["name"] for c in corners if ci in c["corners"]),
                              "best_deg": round(mine, 1) if mine == mine else None, "max_deg": round(top[0], 1),
                              "max_from": f"{top[1]['s']['name']} lap {top[1]['num']}", "sessions": by_session})
        m_fast = in_lap(fast)
        f_lat, f_lon, f_d = lat_of(fast), lon_of(fast), np.clip(fast["prog"], 0.0, L_ref)
        corners_out = []
        for c in corners:
            m = m_fast & in_sec(f_d, c, L_ref)
            if m.sum() < 5:
                continue
            apex_i = np.flatnonzero(m)[int(np.argmax(np.abs(f_lat[m])))]
            run = next(r for r in runs(m) if r[0] <= apex_i <= r[-1])      # the stretch of the lap that holds the apex
            g = lap_views.corner_grip(f_lat[run], f_lon[run], env, fast["s"]["dt"])
            if g is None:
                continue
            share = lap_views.limit_share(f_lat[m], f_lon[m], env)
            corners_out.append({"name": c["name"], "near_limit_pct": round(float(100 * np.mean(share >= lap_views.NEAR_LIMIT))),
                                "entry_blend_pct": None if g["entry_blend_pct"] is None else round(g["entry_blend_pct"]),
                                "peak_lat_g": round(g["peak_lat_g"], 2), "peak_brake_g": round(g["peak_brake_g"], 2)})
        gyro = None
        if all(L["s"]["gyro"] for L in used):
            rate, rolls = [], {"RollRate": [], "YawRate": [], "PitchRate": []}
            for L in used:
                s_, m = L["s"], in_lap(L)
                lean_signed = np.degrees(np.arctan(s_["gld"][L["idx"]]))
                rate.append(np.gradient(lean_signed, L["t"])[m])
                for k in rolls:
                    rolls[k].append(smooth(s_["gyro"][k][L["idx"]], s_["dt"], 0.8)[m])
            gyro = lap_views.gyro_agreement(np.concatenate(rate), *(np.concatenate(rolls[k]) for k in rolls))
            if gyro:
                gyro["r"] = round(gyro["r"], 2)
        thin = slice(None, None, 2)
        c100 = lambda arr: [int(round(float(x) * 100)) for x in arr]
        grip = {"lean_win_m": lap_views.LEAN_WIN_M, "hold_s": lap_views.HOLD_S, "near_limit": lap_views.NEAR_LIMIT,
                "laps": len(used), "turns": turns_out, "corners": corners_out, "gyro": gyro,
                "envelope": {"step_deg": lap_views.ENV_STEP_DEG, "width_deg": lap_views.ENV_WIDTH_DEG,
                             "hold_s": lap_views.ENV_HOLD_S, "lap_pct": lap_views.ENV_LAP_PCT, "g100": c100(env)},
                "gg": {"d": [round(float(x), 1) for x in f_d[m_fast][thin]], "lat100": c100(f_lat[m_fast][thin]),
                       "lon100": c100(f_lon[m_fast][thin])}}

    # engine view: only when the logs carry ECU channels. A channel counts when every session has it and
    # it moves; one that is there but flat is reported as such and never drawn.
    def role_state(role):
        have = [s_["eng"].get(role) for s_ in sessions]
        if not any(have):
            return {"name": None, "state": "missing", "note": ""}
        name = next(h["name"] for h in have if h)
        if not all(have):
            return {"name": name, "state": "partial", "note": "not in every session"}
        flat = [h for h in have if h["state"] != "ok"]
        if flat:
            return {"name": name, "state": "flat", "note": flat[0]["note"]}
        return {"name": name, "state": "ok", "note": ""}

    chan = {role: role_state(role) for role in lap_views.ROLE_NAMES}
    live = {role for role, c in chan.items() if c["state"] == "ok"}
    engine = None
    if any(c["state"] != "missing" for c in chan.values()):
        engine = {"channels": chan, "gear_from": None, "gear_note": "", "gears": [], "laps": {}, "corners": [],
                  "full_throttle_pct": None}
    if live:
        for s_ in sessions:
            s_["thr"] = lap_views.percent_of_range(s_["eng"]["throttle"]["raw"]) if "throttle" in live else None
            s_["brk"] = lap_views.percent_of_range(s_["eng"]["brake"]["raw"]) if "brake" in live else None
            s_["rpm"] = s_["eng"]["rpm"]["raw"] if "rpm" in live else None
            s_["gear"] = np.round(s_["eng"]["gear"]["raw"]).astype(int) if "gear" in live else None
        bike = bikes.match(ref["s"]["vehicle"])
        if "gear" in live:
            engine["gear_from"] = "channel"
        elif "rpm" not in live:
            engine["gear_note"] = "No gear channel in these logs."
        elif not (bike and bike["gear_ratios"]):
            engine["gear_note"] = ("No gear channel in these logs. Gear can be worked out from revs and speed once this "
                                   "bike's gearbox ratios are in bikes/ (see scripts/bikes.py).")
        else:       # no gear channel: fit the bike's gearbox to revs and speed, over the whole day's riding
            found = lap_views.gears_from_ratios([(s_["t"], s_["vs"], s_["gld"], s_["eng"]["rpm"]["own_t"], s_["eng"]["rpm"]["own"])
                                                 for s_ in sessions], bike["gear_ratios"])
            if found:
                for s_, g in zip(sessions, found["gears"]):
                    s_["gear"] = g
                engine["gear_from"] = "rpm and speed"
                engine["gear_fit"] = {"pct": round(100 * found["fit"]), "lag_s": found["lag_s"], "tyre_crown": found["tyre_crown"],
                                      "bike": bike["name"]}
                engine["gears"] = [{"gear": i + 1, "rpm_per_kph": round(x, 1)} for i, x in enumerate(found["rpm_per_kph"])]
            else:
                engine["gear_note"] = (f"No gear channel in these logs, and the revs do not fit the {bike['name']} gearbox "
                                       "in bikes/ clearly enough to work the gears out.")
        egrid = lap_views.lap_grid(L_ref, 5.0)

        def traces(L):
            s_, i = L["s"], L["idx"]
            lin = lambda arr: np.interp(egrid, L["prog"], arr[i])
            step = np.clip(np.searchsorted(L["prog"], egrid), 0, len(i) - 1)        # gear: the sample there, not a blend
            return {"kph": lin(s_["vs"]) * 3.6, "t": lap_views.clock_at(curve(L), egrid),
                    "rpm": lin(s_["rpm"]) if s_["rpm"] is not None else None,
                    "gear": s_["gear"][i][step] if s_["gear"] is not None else None,
                    "thr": lin(s_["thr"]) if s_["thr"] is not None else None,
                    "brk": lin(s_["brk"]) if s_["brk"] is not None else None}

        key_of = lambda L: f"{L['s']['name']}:{L['num']}"
        tr = {key_of(L): traces(L) for L in [fast] + [c["best_lap"] for c in corners]}

        def corner_numbers(L, c):
            T = tr[key_of(L)]
            m = in_sec(egrid, c, L_ref)
            j = np.flatnonzero(m)
            if c["wrap"]:                               # ride order: the end of the lap, then its start
                j = np.concatenate([j[egrid[j] >= c["d0"]], j[egrid[j] <= c["d1"]]])
            if len(j) < 3:
                return None
            along = np.where(egrid[j] >= c["d0"], egrid[j] - c["d0"], egrid[j] + L_ref - c["d0"]) if c["wrap"] else egrid[j] - c["d0"]
            a = int(np.argmin(T["kph"][j]))
            out = {"apex_m": round(float(egrid[j][a]), 1), "apex_kph": round(float(T["kph"][j][a]), 1)}
            if T["rpm"] is not None:
                out.update(apex_rpm=int(round(float(T["rpm"][j][a]), -1)), min_rpm=int(round(float(T["rpm"][j].min()), -1)),
                           exit_rpm=int(round(float(T["rpm"][j][-1]), -1)))
            if T["gear"] is not None:
                out["apex_gear"] = int(T["gear"][j][a])
            if T["thr"] is not None:
                on = lap_views.throttle_pickup_m(along, T["thr"][j], float(along[a]))
                tt = np.where(egrid[j] >= c["d0"], T["t"][j], T["t"][j] + T["t"][-1]) if c["wrap"] else T["t"][j]
                out["throttle_on_m"] = None if on is None else round(on, 0)
                out["coast_s"] = round(lap_views.coast_seconds(tt, T["thr"][j], T["brk"][j] if T["brk"] is not None else None), 2)
            return out

        ints = lambda arr, unit=1: None if arr is None else [int(round(float(x) / unit)) * unit for x in arr]
        engine["step_m"] = 5.0
        engine["d"] = [round(float(x), 1) for x in egrid]
        engine["best"] = key_of(fast)
        engine["laps"] = {k: {"kph": [round(float(x), 1) for x in T["kph"]], "rpm": ints(T["rpm"], 10), "gear": ints(T["gear"]),
                              "thr": ints(T["thr"]), "brk": ints(T["brk"])} for k, T in tr.items()}
        engine["corners"] = [{"name": c["name"], "fastest": key_of(c["best_lap"]), "best_lap": corner_numbers(fast, c),
                              "fastest_run": corner_numbers(c["best_lap"], c)} for c in corners]
        if fast["s"]["thr"] is not None:
            engine["full_throttle_pct"] = round(lap_views.full_throttle_pct(fast["s"]["thr"][fast["idx"]][in_lap(fast)]), 1)

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
        "sections": [{"id": sec["id"], "name": sec["name"], "part": sec["part"], "side": sec["side"], "d0": round(sec["d0"], 1),
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
        "delta": delta, "stint": {"min_trend_laps": lap_views.MIN_TREND_LAPS, "sessions": stint}, "grip": grip,
        "engine": engine,
    }, open(os.path.join(a.out, "viewer.json"), "w"), separators=(",", ":"))

    print("\n".join(lines))


if __name__ == "__main__":
    main()
