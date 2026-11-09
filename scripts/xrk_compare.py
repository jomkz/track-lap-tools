#!/usr/bin/env python3
"""Compare two sets of laps in the same direction (two bikes, or two days) piece by piece.

Usage:
    python3 -I xrk_compare.py OUT_DIR --a "Ninja 400" A1.xrk A2.xrk --b "R3" B1.xrk B2.xrk [--turns FILE.csv]

Both sets must be from the same track. Turns come from the track's own turn table
(tracks/<id>/turns.csv) or from --turns (for example a day's corners.csv at a track with no table).
The lap is cut at every turn and at the top speed between turns, so each turn has an
"into" piece (top speed to the turn: braking and entry) and a "drive out" piece (the turn to the
next top speed: exit and straight). For each piece it gives each set's best-lap time and its ideal
(fastest of any lap within 3% of that set's best), and the difference (negative = A quicker).

Writes compare.csv and compare.md.
"""
import argparse
import csv
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import track_common as tc  # noqa: E402


def lap_times_at(ref, lap, marks):
    d, off, _ = ref.project(lap)
    t = lap["s"]["t"][lap["i"]] - lap["s"]["t"][lap["i"]][0]
    n = len(d)
    q = max(1, n // 4)
    d = d.copy()
    head, tail = d[:q], d[-q:]
    head[head > ref.L - 300] = 0
    tail[tail < 300] += ref.L
    d[:q], d[-q:] = head, tail
    d = np.maximum.accumulate(d)
    tb = np.interp(marks, d, t)
    return np.diff(np.r_[0.0, tb, lap["time"]])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--a", nargs="+", required=True, metavar=("LABEL", "FILE"))
    ap.add_argument("--b", nargs="+", required=True, metavar=("LABEL", "FILE"))
    ap.add_argument("--turns", help="turn table to cut the lap at (turn,name,lat,lon, or a day's corners.csv)")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    loaded = {key: [tc.read(f) for f in arg[1:]] for key, arg in (("A", a.a), ("B", a.b))}
    tc.place(loaded["A"] + loaded["B"])      # one map for both sets
    sets = {key: {"label": arg[0], "laps": tc.full_laps(loaded[key])} for key, arg in (("A", a.a), ("B", a.b))}
    bestA = min(sets["A"]["laps"], key=lambda l: l["time"])
    dirn = bestA["dir"]
    ref = tc.Ref(bestA)
    T = sorted(tc.turns(ref, a.turns), key=lambda t: t["d"])
    if not T:
        sys.exit(tc.NO_TURNS)
    grid = np.arange(0, ref.L, 1.0)
    vA = tc.on_grid(ref, bestA, grid, "vs")
    # each turn's mark is A's slowest point within 80 m of the turn's position
    apex = []
    for t in T:
        m = np.abs(grid - t["d"]) < 80
        apex.append(float(grid[m][np.argmin(vA[m])]))
    marks, names, kinds = [], [], []
    for k, t in enumerate(T):
        nxt = T[(k + 1) % len(T)]
        lo, hi = apex[k], apex[(k + 1) % len(T)]
        m = (grid > lo) & (grid < hi) if hi > lo else (grid > lo)
        peak = float(grid[m][np.argmax(vA[m])]) if m.any() else lo
        # piece starting at the turn = drive out of it; piece starting at the top speed = into the next
        marks += [lo, peak]
        names += [f"Drive out of {t['label']}", f"Into {nxt['label']}"]
        kinds += ["drive", "into"]
    marks = np.array(marks)
    order = np.argsort(marks)
    marks, names, kinds = marks[order], [names[i] for i in order], [kinds[i] for i in order]
    # piece 0 runs from the line to the first mark; it belongs to the last piece (wraps the line)
    res = {}
    for key, S in sets.items():
        laps = [l for l in S["laps"] if l["dir"] == dirn]
        if not laps:
            sys.exit(f"{S['label']}: no {dirn} laps")
        best = min(laps, key=lambda l: l["time"])
        used = [l for l in laps if l["time"] <= best["time"] * 1.03]
        P = []
        for l in used:
            p = lap_times_at(ref, l, marks)
            P.append(np.r_[p[1:-1], p[0] + p[-1]])
        P = np.array(P)
        bi = int(np.argmin([l["time"] for l in used]))
        res[key] = {"ideal": P.min(0), "best": P[bi], "best_lap": f"{used[bi]['s']['name']} lap {used[bi]['num']}",
                    "best_time": used[bi]["time"], "n": len(used),
                    "top_kph": float(np.median([l["s"]["vs"][l["i"]].max() * 3.6 for l in used])),
                    "vehicle": used[bi]["s"]["vehicle"], "date": used[bi]["s"]["date"]}
    pieces = list(names)
    # the line sits inside the last piece; its time is the last part plus the first part
    A, B = res["A"], res["B"]
    rows = []
    for k, nm in enumerate(pieces):
        rows.append({"piece": nm, "kind": kinds[k], "A_best_s": round(A["best"][k], 2), "A_ideal_s": round(A["ideal"][k], 2),
                     "B_best_s": round(B["best"][k], 2), "B_ideal_s": round(B["ideal"][k], 2),
                     "ideal_diff_s": round(A["ideal"][k] - B["ideal"][k], 2), "best_diff_s": round(A["best"][k] - B["best"][k], 2)})
    with open(os.path.join(a.out, "compare.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    la, lb = sets["A"]["label"], sets["B"]["label"]
    tot = lambda kind, key: sum(r[key] for r in rows if r["kind"] == kind)
    lines = [f"# {la} vs {lb}, {dirn}", "",
             f"- {la}: {A['date']}, best {A['best_time']:.2f} s ({A['best_lap']}), ideal {A['ideal'].sum():.2f} s, {A['n']} laps, median top speed {A['top_kph']:.0f} km/h",
             f"- {lb}: {B['date']}, best {B['best_time']:.2f} s ({B['best_lap']}), ideal {B['ideal'].sum():.2f} s, {B['n']} laps, median top speed {B['top_kph']:.0f} km/h",
             f"- Drive-out pieces (exits and straights): {la} {tot('drive', 'ideal_diff_s'):+.2f} s vs {lb} (ideal), {tot('drive', 'best_diff_s'):+.2f} s (best laps)",
             f"- Into pieces (braking and entry): {la} {tot('into', 'ideal_diff_s'):+.2f} s vs {lb} (ideal), {tot('into', 'best_diff_s'):+.2f} s (best laps)",
             "- Negative means " + la + " is quicker. Pieces are cut on " + la + "'s best lap.", "",
             f"| Piece | {la} ideal | {lb} ideal | Diff | Best-lap diff |", "|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['piece']} | {r['A_ideal_s']:.2f} | {r['B_ideal_s']:.2f} | {r['ideal_diff_s']:+.2f} | {r['best_diff_s']:+.2f} |")
    open(os.path.join(a.out, "compare.md"), "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
