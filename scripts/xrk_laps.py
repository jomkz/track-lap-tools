#!/usr/bin/env python3
"""Read lap times from AiM .xrk / .xrz files (Solo DL, Race Studio 3 logs).

Usage:
    python3 -I xrk_laps.py OUT_DIR FILE.xrk [FILE.xrk ...]

Writes to OUT_DIR:
    laps_all.csv        one row per lap, every session
    sessions.csv        one row per session (lap count, best, consistency, fade)
    <session>.csv       one lap table per session file

Needs: python3 -m pip install libxrk   (https://github.com/m3rlin45/libxrk)

Definitions
    out / in laps   : as the logger marks them (pit exit / pit entry). Excluded from stats.
    clean lap       : a full lap no slower than 107% of that session's median full lap
                      (drops laps with traffic, a slow-down or a stop).
    spread          : slowest clean lap minus best lap.
    std dev         : standard deviation of clean laps.
    fade            : average of the last third of clean laps minus the first third
                      (positive = slower late in the session).
    direction       : CW or CCW, read from the GPS track. Each full lap's path is a closed
                      loop; the sign of its enclosed area (shoelace formula on longitude and
                      latitude) gives the way round. Session direction = most laps' direction.
                      Some tracks run both ways, so compare lap times only within one direction.
    track           : the known track (tracks/<id>/) the GPS points are at, blank when there is
                      no folder for it. venue is the track name set in the logger.
    venue_direction : the direction the logger's track name implies, as a cross-check only, for
                      tracks that list it ("logger_track_names" in track.json: at Harris Hill
                      the AiM track reads "R2H" counterclockwise and "H2R" clockwise). Blank
                      otherwise.
    direction_check : "match", or "MISMATCH" when GPS and track name disagree (GPS wins;
                      the logger was probably left on the other track setting).
"""
import csv
import os
import statistics as st
import sys
from datetime import datetime

from libxrk import aim_xrk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tracks  # noqa: E402

SLOW_FACTOR = 1.07


def fmt(sec):
    if sec is None:
        return ""
    m, s = divmod(sec, 60)
    return f"{int(m)}:{s:06.3f}"


def lap_direction(times, lats, lons, start, end):
    """CW or CCW for one lap from its GPS points, or "" if there are too few."""
    pts = [(lons[i], lats[i]) for i, tc in enumerate(times)
           if start <= tc < end and lats[i] and lons[i]]
    if len(pts) < 10:
        return ""
    area = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        area += x1 * y2 - x2 * y1
    return "CCW" if area > 0 else "CW"


def read_session(path):
    import contextlib
    import io

    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        log = aim_xrk(path)
    md = log.metadata
    try:
        dt = datetime.strptime(f"{md.get('Log Date')} {md.get('Log Time')}", "%m/%d/%Y %H:%M:%S")
    except (TypeError, ValueError):
        dt = None
    laps = log.laps.to_pylist()
    try:
        lat_t = log.channels["GPS Latitude"]
        lon_t = log.channels["GPS Longitude"]
        g_times = lat_t.column(0).to_pylist()
        g_lat = lat_t.column(1).to_pylist()
        g_lon = lon_t.column(1).to_pylist()
    except (KeyError, IndexError):
        g_times = g_lat = g_lon = []
    fixes = [(a, b) for a, b in zip(g_lat, g_lon) if a and b]
    track = tracks.match(st.median(a for a, _ in fixes), st.median(b for _, b in fixes)) if fixes else None
    full = [(l["end_time"] - l["start_time"]) / 1000 for l in laps if l["lap_type"] == "full"]
    med = st.median(full) if full else None
    rows = []
    elapsed = 0.0
    for l in laps:
        t = (l["end_time"] - l["start_time"]) / 1000
        elapsed = l["end_time"] / 1000
        kind = l["lap_type"]
        clean = kind == "full" and med is not None and t <= med * SLOW_FACTOR
        d = lap_direction(g_times, g_lat, g_lon, l["start_time"], l["end_time"]) if kind == "full" else ""
        rows.append({
            "lap": l["num"], "type": kind, "direction": d, "lap_s": round(t, 3), "lap_time": fmt(t),
            "clean": clean, "elapsed_min": round(elapsed / 60, 2),
        })
    best = min((r["lap_s"] for r in rows if r["type"] == "full"), default=None)
    for r in rows:
        r["gap_to_best_s"] = round(r["lap_s"] - best, 3) if best is not None and r["type"] == "full" else ""
    dirs = [r["direction"] for r in rows if r["direction"]]
    direction = max(set(dirs), key=dirs.count) if dirs else ""
    c = [r["lap_s"] for r in rows if r["clean"]]
    n3 = max(1, len(c) // 3)
    summary = {
        "session": os.path.splitext(os.path.basename(path))[0],
        "date": dt.strftime("%Y-%m-%d") if dt else md.get("Log Date"),
        "start": dt.strftime("%H:%M") if dt else md.get("Log Time"),
        "venue": md.get("Venue"), "track": track["name"] if track else "", "vehicle": md.get("Vehicle"),
        "direction": direction,
        "venue_direction": (track["logger_track_names"] if track else {}).get((md.get("Venue") or "").strip().upper(), ""),
        "on_track_min": round(elapsed / 60, 1),
        "laps_total": len(rows), "laps_full": len(full), "laps_clean": len(c),
        "best": fmt(best), "best_s": best,
        "avg_clean": fmt(st.mean(c)) if c else "",
        "std_dev_s": round(st.stdev(c), 2) if len(c) > 1 else "",
        "spread_s": round(max(c) - best, 2) if c else "",
        "within_1pct_of_best": sum(1 for x in c if x <= best * 1.01) if c else 0,
        "fade_s": round(st.mean(c[-n3:]) - st.mean(c[:n3]), 2) if len(c) >= 3 else "",
        "best_lap_no": next((r["lap"] for r in rows if r["type"] == "full" and r["lap_s"] == best), ""),
    }
    vd = summary["venue_direction"]
    summary["direction_check"] = "" if not (vd and direction) else ("match" if vd == direction else "MISMATCH")
    return summary, rows


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    sessions, all_rows = [], []
    for p in sorted(sys.argv[2:]):
        s, rows = read_session(p)
        sessions.append(s)
        with open(os.path.join(out, s["session"] + ".csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        for r in rows:
            all_rows.append({"session": s["session"], "date": s["date"], "start": s["start"], **r})
    sessions.sort(key=lambda s: (s["date"] or "", s["start"] or ""))
    with open(os.path.join(out, "sessions.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(sessions[0].keys()))
        w.writeheader()
        w.writerows(sessions)
    with open(os.path.join(out, "laps_all.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
        w.writeheader()
        w.writerows(all_rows)
    for s in sessions:
        print(f"{s['date']} {s['start']} {s['session']} {s['direction']}: {s['on_track_min']} min, "
              f"{s['laps_full']} full laps ({s['laps_clean']} clean), best {s['best']} (lap {s['best_lap_no']}), "
              f"avg {s['avg_clean']}, sd {s['std_dev_s']}s, spread {s['spread_s']}s, fade {s['fade_s']}s")
        if s["direction_check"] == "MISMATCH":
            print(f"  WARNING {s['session']}: GPS says {s['direction']}, track name {s['venue']} "
                  f"says {s['venue_direction']}. Using GPS.")


if __name__ == "__main__":
    main()
