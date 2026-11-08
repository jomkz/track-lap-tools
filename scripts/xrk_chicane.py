#!/usr/bin/env python3
"""Estimate what the clockwise turn 6 cone chicane costs per lap at Harris Hill.

Usage:
    python3 -I xrk_chicane.py OUT_DIR FILE.xrk [FILE.xrk ...]        (clockwise laps, one bike)
    python3 -I xrk_chicane.py --make-radius CCW_FILE.xrk [...]        (rebuild t6_natural_radius.csv)

Clockwise, cones in turn 6 make him brake between turn 5 and Mustang (turn 7).
Counterclockwise it is flat out, so the counterclockwise laps give the natural line's radius.

Model, per clockwise lap, from his top speed after turn 5 to his slowest point in turn 7:
    no chicane : full throttle from that top speed, using this bike's own measured acceleration at
                 each speed (70th and 90th percentile of upright drive samples that day), never faster
                 through turn 6 than its natural radius allows at a lean of 1.0, 1.1 or 1.2 g, then
                 braking for turn 7 at the decel he used on that lap, to the same turn 7 speed.
    cost       : his actual time over that stretch minus the modelled time.
Reported as a range over the six cases and over his laps; it is an estimate, not a measurement.

Writes chicane_laps.csv, chicane.md and chicane.png to OUT_DIR.
"""
import csv
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import track_common as tc  # noqa: E402

RADIUS_CSV = os.path.join(tc.HERE, "t6_natural_radius.csv")
GMAX = (1.0, 1.1, 1.2)
ACCEL_P = (70, 90)
SLOW_FACTOR = 1.03


def make_radius(files):
    sess = [tc.load(f) for f in files]
    laps = [l for l in tc.full_laps(sess) if l["dir"] == "CCW"]
    best = min(laps, key=lambda l: l["time"])
    laps = [l for l in laps if l["time"] <= best["time"] * 1.02]
    ref = tc.Ref(best)
    T = {t["turn"]: t["d"] for t in tc.turns(ref)}
    lo, hi = sorted((T[5], T[7]))
    grid = np.arange(lo, hi, 2.0)
    R = []
    for l in laps:
        v = tc.on_grid(ref, l, grid, "vs")
        g = np.abs(tc.on_grid(ref, l, grid, "gl")) * tc.G
        R.append(v ** 2 / np.maximum(g, 0.3 * tc.G))
    r = np.median(np.array(R), axis=0)
    idx = np.searchsorted(ref.d, grid).clip(0, len(ref.d) - 1)
    lat0, lon0 = tc.LAT0, tc.LON0
    with open(RADIUS_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["lat", "lon", "radius_m"])
        s, i = best["s"], best["i"]
        for k, j in enumerate(idx):
            w.writerow([f"{s['lat'][i][j]:.6f}", f"{s['lon'][i][j]:.6f}", f"{r[k]:.0f}"])
    print(f"wrote {RADIUS_CSV} from {len(laps)} CCW laps; tightest {r.min():.0f} m")


def accel_curve(ref, laps, p):
    v = np.concatenate([l["s"]["vs"][l["i"]] * 3.6 for l in laps])
    a = np.concatenate([l["s"]["ax"][l["i"]] for l in laps])
    g = np.concatenate([np.abs(l["s"]["gl"][l["i"]]) for l in laps])
    m = (a > 0.03) & (g < 0.35)
    bins = np.arange(60, 200, 5)
    c = np.array([np.percentile(a[m & (v >= b) & (v < b + 5)], p) if (m & (v >= b) & (v < b + 5)).sum() > 30 else np.nan
                  for b in bins])
    ok = ~np.isnan(c)
    return bins[ok] + 2.5, c[ok]


def model(ref, lap, T, rad, acc, gmax):
    grid = np.arange(T[5], T[7] + 60, 1.0)
    va = tc.on_grid(ref, lap, grid, "vs")
    ax = tc.on_grid(ref, lap, grid, "ax")
    w0 = (grid > T[5] + 50) & (grid < T[6])
    i0 = np.where(w0)[0][np.argmax(va[w0])]
    w1 = (grid > T[7] - 80) & (grid < T[7] + 60)
    i1 = np.where(w1)[0][np.argmin(va[w1])]
    bz = np.where((ax < -0.30) & (np.arange(len(grid)) < i1) & (grid > T[6]))[0]
    ib = bz[0]
    while ib + 1 < i1 and ax[ib + 1] < -0.30:
        ib += 1
    # first sample of the last continuous hard-brake run before turn 7
    j = i1
    while j > i0 and ax[j - 1] >= -0.30:
        j -= 1
    end_b = j
    while j > i0 and ax[j - 1] < -0.30:
        j -= 1
    ib = j
    dec = float(np.median(-ax[ib:end_b])) * tc.G
    vb = np.full_like(grid, np.inf)
    vb[ib:i1 + 1] = va[ib:i1 + 1]
    for i in range(ib - 1, i0 - 1, -1):
        vb[i] = np.sqrt(vb[i + 1] ** 2 + 2 * dec * (grid[i + 1] - grid[i]))
    cap = np.full_like(grid, np.inf)
    cap[:ib] = np.sqrt(gmax * tc.G * rad[:ib])
    bv, bc = acc
    vf = np.full_like(grid, np.nan)
    vf[i0] = va[i0]
    for i in range(i0 + 1, i1 + 1):
        a = np.interp(vf[i - 1] * 3.6, bv, bc) * tc.G
        vf[i] = min(cap[i], np.sqrt(vf[i - 1] ** 2 + 2 * max(a, 0) * (grid[i] - grid[i - 1])))
    vm = np.minimum(vf, vb)
    s = slice(i0, i1 + 1)
    tt = lambda v: float(np.sum(np.diff(grid[s]) / ((v[s][1:] + v[s][:-1]) / 2)))
    t6 = (grid > T[6] - 60) & (grid < T[6] + 40)
    return {"cost": tt(va) - tt(vm), "grid": grid, "va": va, "vm": vm, "i0": i0, "i1": i1,
            "t6_actual": float(va[t6].min() * 3.6), "t6_model": float(np.nanmin(vm[t6]) * 3.6),
            "top_model": float(np.nanmax(vm[s]) * 3.6), "decel_g": dec / tc.G}


def main():
    if sys.argv[1] == "--make-radius":
        return make_radius(sys.argv[2:])
    out, files = sys.argv[1], sys.argv[2:]
    os.makedirs(out, exist_ok=True)
    sess = [tc.load(f) for f in files]
    laps = [l for l in tc.full_laps(sess) if l["dir"] == "CW"]
    if not laps:
        print("no clockwise laps: the chicane is only used clockwise")
        return
    best = min(laps, key=lambda l: l["time"])
    used = sorted([l for l in laps if l["time"] <= best["time"] * SLOW_FACTOR], key=lambda l: l["time"])
    # geometry from the used lap with the smallest GPS shift to the others (steadier corners)
    ref = tc.Ref(best)
    T = {t["turn"]: t["d"] for t in tc.turns(ref)}
    rows = list(csv.DictReader(open(RADIUS_CSV)))
    rd = ref.dist_of(np.array([float(r["lat"]) for r in rows]), np.array([float(r["lon"]) for r in rows]))
    rr = np.array([float(r["radius_m"]) for r in rows])
    o = np.argsort(rd)
    grid = np.arange(T[5], T[7] + 60, 1.0)
    rad = np.interp(grid, rd[o], rr[o], left=1e4, right=1e4)
    accs = {p: accel_curve(ref, laps, p) for p in ACCEL_P}
    res = []
    for l in used:
        r = {"session": l["s"]["name"], "lap": l["num"], "lap_s": round(l["time"], 3)}
        for g in GMAX:
            for p in ACCEL_P:
                m = model(ref, l, T, rad, accs[p], g)
                r[f"cost_s_lean{g}_acc{p}"] = round(m["cost"], 3)
        m = model(ref, l, T, rad, accs[90], 1.1)
        r.update({"t6_min_kph": round(m["t6_actual"], 1), "t6_model_kph": round(m["t6_model"], 1),
                  "top_before_t7_model_kph": round(m["top_model"], 1), "t7_decel_g": round(m["decel_g"], 2)})
        res.append(r)
    with open(os.path.join(out, "chicane_laps.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(res[0].keys()))
        w.writeheader()
        w.writerows(res)
    keys = [k for k in res[0] if k.startswith("cost_s")]
    C = np.array([[r[k] for k in keys] for r in res])
    med = np.median(C, axis=0)
    lo, hi = float(med.min()), float(med.max())
    central = float(np.median(C[:, keys.index("cost_s_lean1.1_acc90")]))
    b = res[0]
    vehicle = best["s"]["vehicle"]
    lines = [f"# Turn 6 chicane cost, {best['s']['date']} ({vehicle}, CW)", "",
             f"- Estimated cost per lap: about {central:.1f} s (range {lo:.2f} to {hi:.2f} s over the model cases, median of {len(res)} laps).",
             f"- Best lap {b['session']} lap {b['lap']}: slowest in turn 6 {b['t6_min_kph']:.0f} km/h; modelled without cones "
             f"{b['t6_model_kph']:.0f} km/h, peaking near {b['top_before_t7_model_kph']:.0f} km/h before braking for Mustang.",
             "- Method: see the header of scripts/xrk_chicane.py. Turn 6's natural radius comes from counterclockwise laps",
             "  (scripts/t6_natural_radius.csv); acceleration is this bike's own from the same day. Estimate, not a measurement.",
             "", "| Lean limit through 6 | Accel 70th pct | Accel 90th pct |", "|---|---|---|"]
    for g in GMAX:
        lines.append(f"| {g} g | {np.median(C[:, keys.index(f'cost_s_lean{g}_acc70')]):.2f} s | "
                     f"{np.median(C[:, keys.index(f'cost_s_lean{g}_acc90')]):.2f} s |")
    open(os.path.join(out, "chicane.md"), "w").write("\n".join(lines) + "\n")
    # chart: best lap actual vs modelled
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    m = model(ref, used[0], T, rad, accs[90], 1.1)
    s = slice(m["i0"] - 40 if m["i0"] > 40 else 0, m["i1"] + 40)
    fig, ax = plt.subplots(figsize=(9, 4.2), facecolor="#fcfcfb")
    ax.set_facecolor("#fcfcfb")
    ax.plot(m["grid"][s] - m["grid"][0], m["va"][s] * 3.6, color="#2a78d6", lw=2, label="Actual, with cones")
    ax.plot(m["grid"][s] - m["grid"][0], m["vm"][s] * 3.6, color="#eb6834", lw=2, ls="--", label="Modelled, no cones")
    for t, nm in ((6, "T6"), (7, "T7 Mustang")):
        ax.axvline(T[t] - m["grid"][0], color="#888", lw=0.8)
        ax.text(T[t] - m["grid"][0] + 4, ax.get_ylim()[0] + 4, nm, color="#555", fontsize=9)
    ax.set_xlabel("Metres after turn 5")
    ax.set_ylabel("Speed (km/h)")
    ax.set_title(f"Turn 6 chicane, best lap ({used[0]['s']['name']} lap {used[0]['num']}): about {m['cost']:.1f} s", loc="left", fontsize=11)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    ax.legend(frameon=False, loc="upper right")
    fig.tight_layout()
    fig.savefig(os.path.join(out, "chicane.png"), dpi=150)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
