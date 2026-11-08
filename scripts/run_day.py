#!/usr/bin/env python3
"""One command for a track day: lap times, ideal lap, where time is lost, tire check, chicane cost.

Usage:
    python3 -I scripts/run_day.py 2026-11-07                       (every .xrk in raw/2026-11-07/)
    python3 -I scripts/run_day.py 2026-11-07 --vs 2026-11-06        (also compare with that day)
    python3 -I scripts/run_day.py path/to/a_0225.xrk path/to/a_0226.xrk

Files from one day can mix bikes and directions: they are grouped by bike and direction (from GPS),
and each group gets its own folder, output/analysis_<date>[_<bike>_<dir>]/ (no suffix when the day has
one group). --vs compares each group with the laps in the --vs files that share its direction.
Steps per group: xrk_laps.py (lap table, direction, consistency, fade), xrk_ideal_lap.py (ideal lap,
corners, map, speed trace), xrk_tire.py (tire-wear signal), xrk_chicane.py (clockwise only),
xrk_compare.py (with --vs). Turn numbers are the official Harris Hill ones in both directions.
"""
import argparse
import csv
import os
import re
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import track_common as tc  # noqa: E402

ROOT = os.path.dirname(tc.HERE)
PY = [sys.executable, "-I"]


def run(*args):
    print("+", " ".join(os.path.basename(a) if a.endswith(".xrk") else a for a in args[2:]), flush=True)
    r = subprocess.run(list(args), capture_output=True, text=True)
    if r.returncode:
        print(r.stdout[-2000:], r.stderr[-4000:])
        raise SystemExit(f"step failed: {args[2]}")
    return r.stdout


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "bike").lower()).strip("-")


def groups(files):
    g = {}
    for f in files:
        s = tc.load(f)
        laps = tc.full_laps([s])
        if not laps:
            print(f"skip {os.path.basename(f)}: no full laps")
            continue
        dirs = [l["dir"] for l in laps]
        d = max(set(dirs), key=dirs.count)
        date = datetime.strptime(s["date"], "%m/%d/%Y").strftime("%Y-%m-%d") if s["date"] else "undated"
        g.setdefault((date, s["vehicle"], d), []).append(f)
    return g


def ideal_with_good_corners(out, files, laps):
    """Run the ideal lap with the fastest lap as geometry; if its corners come out muddled (an
    official turn found twice, which a GPS-shifted lap can cause), retry with the next fastest."""
    tried = []
    for l in sorted(laps, key=lambda l: l["time"])[:6]:
        ref = f"{l['s']['name']}:{l['num']}"
        tried.append(ref)
        run(*PY, os.path.join(tc.HERE, "xrk_ideal_lap.py"), out, *files, "--ref", ref)
        nums = []
        for r in csv.DictReader(open(os.path.join(out, "corners.csv"))):
            nums += re.findall(r"T(\d+)", r["corner"])
        if len(nums) == len(set(nums)):
            return ref
        print(f"  corners muddled with {ref} as geometry; trying the next lap")
    return tried[-1]


def expand(args):
    """A YYYY-MM-DD argument means every .xrk in raw/<date>/."""
    out = []
    for x in args:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", x):
            d = os.path.join(ROOT, "raw", x)
            found = sorted(os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith(".xrk")) if os.path.isdir(d) else []
            if not found:
                raise SystemExit(f"no .xrk files in {d}")
            out += found
        else:
            out.append(x)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--vs", nargs="+", default=[], help="other .xrk files to compare against (same direction)")
    ap.add_argument("--out", default=os.path.join(ROOT, "output"))
    a = ap.parse_args()
    G = groups(expand(a.files))
    VS = groups(expand(a.vs)) if a.vs else {}
    index = []
    for (date, bike, d), files in sorted(G.items()):
        one = sum(1 for k in G if k[0] == date) == 1
        out = os.path.join(a.out, f"analysis_{date}" + ("" if one else f"_{slug(bike)}_{d.lower()}"))
        os.makedirs(out, exist_ok=True)
        print(f"== {date} {bike} {d}: {len(files)} files -> {out}")
        run(*PY, os.path.join(tc.HERE, "xrk_laps.py"), os.path.join(out, "laps"), *files)
        sess = [tc.load(f) for f in files]
        laps = [l for l in tc.full_laps(sess) if l["dir"] == d]
        best = min(l["time"] for l in laps)
        laps = [l for l in laps if l["time"] <= best * 1.03]
        ref = ideal_with_good_corners(out, files, laps)
        run(*PY, os.path.join(tc.HERE, "xrk_tire.py"), out, *files)
        if d == "CW":
            run(*PY, os.path.join(tc.HERE, "xrk_chicane.py"), out, *files)
        for (vdate, vbike, vd), vfiles in sorted(VS.items()):
            if vd == d:
                cmp_out = os.path.join(out, f"vs_{slug(vbike)}_{vdate}")
                run(*PY, os.path.join(tc.HERE, "xrk_compare.py"), cmp_out, "--a", bike or "A", *files,
                    "--b", vbike or "B", *vfiles)
        index.append((date, bike, d, out, ref))
    print()
    for date, bike, d, out, ref in index:
        print(f"{date} {bike} {d}: {os.path.relpath(out, ROOT)} (geometry from {ref})")
        for f, lines in (("summary.md", (2, 4, 5)), ("tire_check.md", (4, 5, 6)), ("chicane.md", (2,))):
            p = os.path.join(out, f)
            if os.path.exists(p):
                txt = open(p).read().splitlines()
                for i in lines:
                    if i < len(txt) and txt[i].strip():
                        print("   ", txt[i])


if __name__ == "__main__":
    main()
