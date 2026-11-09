#!/usr/bin/env python3
"""Tire-wear signal: corners where mid-corner speed and grip fall session after session.

Usage:
    python3 -I xrk_tire.py OUT_DIR FILE.xrk [FILE.xrk ...] [--turns FILE.csv]
                                                    (one bike, one direction, one day, one track)

For every turn and every lap within 3% of the day's best, it takes the slowest speed and the peak
lateral g within 70 m of the turn, then the median per session. Turns come from the track's own
turn table (tracks/<id>/turns.csv) when it has one, else from the corners the ideal-lap step found
(OUT_DIR/corners.csv), so run that step first at a track with no table. A turn is flagged when the slowest speed falls by 2 km/h or more from the first session to
the last, never rising by more than 0.5 km/h between sessions, and its peak lateral g falls too.
Harris Hill, Nov 6 check: this flagged 8 Dam Turn and 5 on the R3 with a worn rear, the two corners
where the rider felt the rear stepping out. Inferred from GPS only; there is no slip sensor.

Writes tire_check.csv (per session and turn) and tire_check.md.
"""
import argparse
import csv
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import track_common as tc  # noqa: E402

WIN_M = 70
DROP_KPH = 2.0
SLOW_FACTOR = 1.03


def main():
    ap = argparse.ArgumentParser(description="Tire-wear signal for one bike, one direction, one day.")
    ap.add_argument("out")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--turns", help="turn table to use (turn,name,lat,lon) instead of the track's own")
    a = ap.parse_args()
    out, files = a.out, a.files
    os.makedirs(out, exist_ok=True)
    sess, _ = tc.load_day(files)
    sess.sort(key=lambda s: s["name"])
    laps = tc.full_laps(sess)
    best = min(laps, key=lambda l: l["time"])
    laps = [l for l in laps if l["dir"] == best["dir"] and l["time"] <= best["time"] * SLOW_FACTOR]
    ref = tc.Ref(best)
    T = tc.turns(ref, a.turns, out)
    if not T:
        sys.exit(tc.NO_TURNS)
    rows = []
    for l in laps:
        d, off, _ = ref.project(l)
        s, i = l["s"], l["i"]
        for t in T:
            dd = np.abs((d - t["d"] + ref.L / 2) % ref.L - ref.L / 2)
            m = (dd < WIN_M) & (off < 12)
            if m.sum() < 5:
                continue
            rows.append({"session": s["name"], "lap": l["num"], "turn": t["turn"], "label": t["label"],
                         "min_kph": float(s["vs"][i][m].min() * 3.6),
                         "peak_lat_g": float(np.percentile(np.abs(s["gl"][i][m]), 90))})
    names = [s["name"] for s in sess if any(r["session"] == s["name"] for r in rows)]
    table, flags = [], []
    for t in T:
        per = []
        for n in names:
            rr = [r for r in rows if r["turn"] == t["turn"] and r["session"] == n]
            if rr:
                per.append((n, len(rr), float(np.median([r["min_kph"] for r in rr])),
                            float(np.median([r["peak_lat_g"] for r in rr]))))
        for n, k, v, g in per:
            table.append({"turn": t["label"], "session": n, "laps": k, "min_kph": round(v, 1), "peak_lat_g": round(g, 2)})
        if len(per) >= 2:
            v = [p[2] for p in per]
            g = [p[3] for p in per]
            if v[0] - v[-1] >= DROP_KPH and all(b - a <= 0.5 for a, b in zip(v, v[1:])) and g[-1] < g[0]:
                flags.append((t["label"], v, g))
    with open(os.path.join(out, "tire_check.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["turn", "session", "laps", "min_kph", "peak_lat_g"])
        w.writeheader()
        w.writerows(table)
    lines = [f"# Tire check, {best['s']['date']} ({best['s']['vehicle']}, {best['dir']})", "",
             f"Sessions in order: {', '.join(names)}. Laps within 3% of the best ({len(laps)} laps).", ""]
    if flags:
        lines.append("Turns where mid-corner speed and grip fell every session (likely tire-limited):")
        for lab, v, g in flags:
            lines.append(f"- {lab}: slowest speed {' -> '.join(f'{x:.0f}' for x in v)} km/h, "
                         f"peak lateral g {' -> '.join(f'{x:.2f}' for x in g)}")
    else:
        lines.append("No turn lost mid-corner speed and grip across every session: no tire signal.")
    if len(names) < 2:
        lines.append("Only one session, so there is no trend to read.")
    open(os.path.join(out, "tire_check.md"), "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
