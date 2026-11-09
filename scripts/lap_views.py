"""Numbers for the pit lap viewer's extra views, worked out from laps already placed on the track.

Pure functions on arrays (no file reading), so they are tested on made-up laps (tests/). A lap
here is its "curve": prog, the distance along the reference path at each GPS sample (never
decreasing, 0 at the timing line, a few seconds of padding either side), and t, the sample times.
xrk_ideal_lap.py calls these and writes the results into viewer.json.
"""
import numpy as np

DELTA_STEP_M = 10.0


# ---------------------------------------------------------------- time delta trace

def lap_grid(lap_length, step=DELTA_STEP_M):
    """Distances from the timing line all the way round, every `step` metres, ending on the line."""
    return np.append(np.arange(0.0, lap_length, step), lap_length)


def clock_at(curve, d):
    """Seconds since the lap crossed the timing line, at distance(s) d along the reference."""
    prog, t = curve
    return np.interp(d, prog, t) - float(np.interp(0.0, prog, t))


def pieces_in_order(sections, lap_length):
    """Sections as pieces in riding order from the line. sections: (d0, d1, wrap, key); a section
    that wraps through the timing line becomes two pieces, its end and its start. Raises if the
    pieces do not cover the lap exactly once, because a composite built on them would be wrong."""
    pieces = []
    for d0, d1, wrap, key in sections:
        pieces += [(0.0, d1, key), (d0, lap_length, key)] if wrap else [(d0, d1, key)]
    pieces = sorted((p for p in pieces if p[1] > p[0]), key=lambda p: p[0])
    at = 0.0
    for d0, d1, _ in pieces:
        if abs(d0 - at) > 1e-6:
            raise ValueError(f"sections do not cover the lap: {at:.1f} m to {d0:.1f} m is "
                             f"{'missing' if d0 > at else 'covered twice'}")
        at = d1
    if abs(at - lap_length) > 1e-6:
        raise ValueError(f"sections do not cover the lap: they end at {at:.1f} m of {lap_length:.1f} m")
    return pieces


def composite_clock(grid, pieces, curves):
    """The clock of a lap stitched from pieces of other laps: each piece (d0, d1, key) is ridden
    at the pace of curves[key]. Returns (clock on grid, total time). With each piece taken from
    the lap that was fastest through it, this is the ideal lap."""
    out = np.zeros(len(grid))
    base = 0.0
    for d0, d1, key in pieces:
        start = float(clock_at(curves[key], d0))
        m = (grid >= d0) & (grid <= d1)
        out[m] = base + clock_at(curves[key], grid[m]) - start
        base += float(clock_at(curves[key], d1)) - start
    return out, base


def delta_cs(clock, ref_clock):
    """Time lost to the reference lap so far, in hundredths of a second (positive = behind)."""
    return [int(round(float(x) * 100)) for x in np.asarray(clock) - np.asarray(ref_clock)]


# ---------------------------------------------------------------- stint view

MIN_TREND_LAPS = 5


def robust_trend(x, y, min_points=MIN_TREND_LAPS):
    """A line through lap times (y, seconds) against time on track (x, minutes) that one slow lap
    cannot tilt: the Theil-Sen line, whose slope is the median of the slopes between every pair of
    laps and whose level is the median of what is left. Least squares would let a single lap in
    traffic set the slope. Returns {"s_per_min", "at_0_s", "n"}, or None with fewer than
    min_points laps (a slope through a handful of laps says nothing).
    It is a guide for the eye, not a rate to quote: a session whose pace steps once (a new tire,
    a change of line) has no single slope, and the line will lie somewhere between the two levels."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    if len(x) < min_points:
        return None
    i, j = np.triu_indices(len(x), 1)
    ok = x[j] != x[i]
    if not ok.any():
        return None
    slope = float(np.median((y[j][ok] - y[i][ok]) / (x[j][ok] - x[i][ok])))
    return {"s_per_min": slope, "at_0_s": float(np.median(y - slope * x)), "n": int(len(x))}


def late_minus_early(section_times, n_third):
    """Per section: the mean over the last n_third laps minus the mean over the first n_third.
    section_times: one row of section times per lap, in the order ridden. None if n_third is 0."""
    rows = np.asarray(section_times, float)
    if not n_third or len(rows) < 2 * n_third:
        return None
    return rows[-n_third:].mean(axis=0) - rows[:n_third].mean(axis=0)


# ---------------------------------------------------------------- grip view (lean and the grip circle)

LEAN_WIN_M = 70.0       # a turn's lean is read within this distance of its apex (as the tire check does)
HOLD_S = 0.5            # ...and only counts if it was held this long, so a GPS spike cannot set it
ENV_STEP_DEG = 15       # the grip outline has a point every this many degrees round the circle
ENV_WIDTH_DEG = 45      # ...read from the samples within half this of that direction
ENV_HOLD_S = 0.2        # ...where a lap's peak is the level it reached for this long
ENV_LAP_PCT = 90        # ...and the outline is this percentile of the laps' peaks
NEAR_LIMIT = 0.9        # "near the limit" = at least this share of the outline in that direction
BRAKING_G = 0.30        # a corner has a brake zone when braking reaches this


def despike(x, samples=5):
    """Take out spikes shorter than about half the window (GPS glitches) and nothing else."""
    from scipy.signal import medfilt
    return medfilt(np.asarray(x, float), samples)


def lean_deg(lat_g):
    """Lean angle (degrees) that balances a cornering force of lat_g: atan(lateral g). This is the
    lean of bike and rider together, measured from upright on a flat road. Hanging off, tire
    width, banking and crests are not in it, so the bike's own lean differs by a few degrees."""
    return np.degrees(np.arctan(np.abs(lat_g)))


def held_peak(x, samples):
    """The largest value of x that was held for at least `samples` samples in a row (nan when x is
    shorter than that). A single high sample does not count; a level that is kept does."""
    from scipy.ndimage import minimum_filter1d
    x = np.asarray(x, float)
    if len(x) < samples or samples < 1:
        return float("nan")
    held = minimum_filter1d(x, size=samples, mode="nearest", origin=0)
    half = samples // 2
    return float(np.max(held[half:len(x) - (samples - 1 - half)]))    # only windows that lie wholly inside x


def envelope(laps, top_n, step_deg=ENV_STEP_DEG, width_deg=ENV_WIDTH_DEG, pct=ENV_LAP_PCT):
    """Outline of the grip the rider reaches, by direction round the grip circle. laps: one
    (lat, lon) pair of arrays per lap; lat = cornering g (right positive), lon = acceleration g
    (braking negative). Returns the radius (g) every step_deg degrees, the first at 0 degrees
    (pure right-hand cornering), going round through 90 (pure acceleration).

    For each direction, each lap gives its own peak: the top_n-th largest of its samples within
    width_deg / 2 of that direction, a level it reached for top_n samples. The outline is the
    pct-th percentile of those peaks across the laps (always one lap's actual peak). So:
      - one glitch cannot set it (a lap's single largest sample is not its peak, and one bad lap
        is not the percentile);
      - it does not creep outward as more laps are ridden, the way the largest sample of the
        whole day would;
      - seconds spent coasting in a direction do not pull it in, as a percentile of samples would.
    The directions overlap because grip does not change sharply with direction while the samples
    can be bunched: hard braking is rarely dead straight. A lap with fewer than 3 x top_n samples
    in a direction has no peak there; a direction no lap has a peak in takes its value from its
    neighbours; nan everywhere if there is none at all."""
    n = int(round(360 / step_deg))
    top_n = max(1, int(top_n))
    peaks = np.full((len(laps), n), np.nan)
    for i, (lat, lon) in enumerate(laps):
        lat, lon = np.asarray(lat, float), np.asarray(lon, float)
        ang = np.degrees(np.arctan2(lon, lat))
        r = np.hypot(lat, lon)
        for k in range(n):
            mine = r[np.abs((ang - k * step_deg + 180.0) % 360.0 - 180.0) <= width_deg / 2.0]
            if len(mine) >= 3 * top_n:
                peaks[i, k] = np.partition(mine, len(mine) - top_n)[len(mine) - top_n]
    out = np.full(n, np.nan)
    for k in range(n):
        col = peaks[:, k][~np.isnan(peaks[:, k])] if len(laps) else []
        if len(col):
            out[k] = np.percentile(col, pct, method="lower")
    ok = ~np.isnan(out)
    if not ok.any():
        return out
    idx = np.arange(n)
    return np.interp(idx, np.r_[idx[ok] - n, idx[ok], idx[ok] + n], np.r_[out[ok], out[ok], out[ok]])


def limit_share(lat, lon, env, step_deg=ENV_STEP_DEG):
    """How much of the outline each sample uses: its grip (g) over the outline in its direction."""
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    n = len(env)
    pos = (np.degrees(np.arctan2(lon, lat)) / step_deg) % n
    lo = np.floor(pos).astype(int) % n
    frac = pos - np.floor(pos)
    edge = env[lo] * (1 - frac) + env[(lo + 1) % n] * frac
    return np.hypot(lat, lon) / edge


def corner_grip(lat, lon, env, dt):
    """Grip use through one corner on one lap, from its samples in riding order.
    near_limit_pct : share of the corner's time spent at NEAR_LIMIT of the outline or more.
    entry_blend_pct: from the hardest braking to the deepest lean, the lowest share of the outline
                     used. Near 100 = braking was traded for lean all the way in (trail braking);
                     low = the brake was let go before the bike was leaned in. None when the
                     corner has no brake zone before its deepest lean."""
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    if len(lat) < 5:
        return None
    share = limit_share(lat, lon, env)
    apex = int(np.argmax(np.abs(lat)))
    out = {"near_limit_pct": float(100 * np.mean(share >= NEAR_LIMIT)), "entry_blend_pct": None,
           "peak_lat_g": float(np.abs(lat[apex])), "peak_brake_g": float(max(0.0, -lon[:apex + 1].min()))}
    if out["peak_brake_g"] >= BRAKING_G:
        brake = int(np.argmin(lon[:apex + 1]))
        if apex > brake:
            out["entry_blend_pct"] = float(100 * min(1.0, share[brake:apex + 1].min()))
    return out


def gyro_agreement(lean_rate, roll, yaw, pitch):
    """Cross-check of the GPS lean against the logger's gyro. The logger sits tilted on the bike, so
    the bike's roll shows up in a mix of its three gyro axes; the mix is fitted, and the result is
    how well the fitted gyro roll rate follows the GPS lean rate: the correlation r, 1 = the same
    shape. Returns {"r", "n"}, or None when there is too little data."""
    y = np.asarray(lean_rate, float)
    A = np.c_[np.asarray(roll, float), np.asarray(yaw, float), np.asarray(pitch, float), np.ones(len(y))]
    ok = np.isfinite(y) & np.all(np.isfinite(A), axis=1)
    if ok.sum() < 500:
        return None
    y, A = y[ok], A[ok]
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    ss = float(np.sum((y - y.mean()) ** 2))
    if ss <= 0:
        return None
    r2 = 1.0 - float(np.sum((y - A @ coef) ** 2)) / ss
    return {"r": float(np.sqrt(max(0.0, r2))), "n": int(ok.sum())}


# ---------------------------------------------------------------- engine view (ECU channels)

ROLE_NAMES = {      # channel names, lower case with only letters and digits, as loggers and ECUs tend to spell them
    "rpm": ("rpm", "enginerpm", "enginespeed", "revs", "erpm"),
    "throttle": ("tps", "throttle", "throttleposition", "throttlepos", "throttleangle", "aps", "pps", "gas", "accelerator"),
    "brake": ("brake", "brakepress", "brakepressure", "brakepos", "brakeposition", "fbrake", "frontbrake", "brakef",
              "fbrakepress", "frontbrakepress", "brakesw", "brakeswitch"),
    "gear": ("gear", "gearposition", "gearpos", "calcgear"),
}
ROLE_FUNCTIONS = {"rpm": ("engine rpm",), "throttle": ("throttle",), "brake": ("brake",), "gear": ("gear",)}
NOT_A_ROLE = ("temp", "volt", "diff", "time", "lambda", "fuel")
TYRE_CROWN = 0.28       # how much the rear tyre's rolling radius shrinks with lean: crown radius over wheel radius
GEAR_MIN_KPH = 18.0
GEAR_MIN_RPM = 2000.0
GEAR_TOL = 0.06         # a sample belongs to a gear when its ratio is within this (6%) of that gear's
GEAR_FIT_TOL = 0.04     # ...and the fit of the gearbox to the data counts samples within this
GEAR_FIT_MIN = 0.75     # the gearbox fits when at least this share of the steady samples sit on a gear
GEAR_FIT_MARGIN = 0.15  # ...and no other line-up of the gears comes within this of it
GEAR_LAGS = (0.1, 0.0, -0.1, -0.2, -0.3, -0.4)      # seconds from GPS speed to the revs that go with it (negative: speed trails)
GEAR_CROWNS = (0.20, 0.28, 0.36, 0.44, 0.52, 0.60)  # values tried for the tyre's shrink with lean
FULL_THROTTLE = 95.0    # percent of the throttle range
THROTTLE_ON = 10.0
BRAKE_ON = 10.0


def channel_roles(meta):
    """Which logged channel is engine speed, throttle, brake and gear. meta: {channel name:
    {"units": .., "function": ..}} as the logger describes them. A channel is taken for a role by
    its name, or by what the logger says its function is. Returns {role: name or None}."""
    out = {role: None for role in ROLE_NAMES}
    for name, m in meta.items():
        key = "".join(ch for ch in name.lower() if ch.isalnum())
        if any(bad in key for bad in NOT_A_ROLE):
            continue
        func = (m.get("function") or "").lower()
        for role in ROLE_NAMES:
            if out[role] is None and (key in ROLE_NAMES[role] or any(f in func for f in ROLE_FUNCTIONS[role])):
                out[role] = name
                break
    return out


def channel_state(values, role):
    """("ok", "") when a channel carries a signal, else ("flat", why): a channel that is in the log
    but never moves (not wired, or not sent by the ECU) must not be shown as data."""
    v = np.asarray(values, float)
    v = v[np.isfinite(v)]
    if len(v) < 50:
        return "flat", "almost no samples"
    if float(v.max() - v.min()) <= 1e-9:
        return "flat", f"always {float(v[0]):.3g}"
    if role == "rpm" and np.percentile(v, 99) < 1500:
        return "flat", f"never above {np.percentile(v, 99):.0f}"
    return "ok", ""


def percent_of_range(values):
    """A throttle or brake channel as 0 to 100 percent of the range it covered that day (its 1st to
    99th percentile), whatever its unit: ECUs send percent, volts, counts or a switch."""
    v = np.asarray(values, float)
    lo, hi = np.nanpercentile(v, 1), np.nanpercentile(v, 99)
    return np.clip((v - lo) / (hi - lo), 0.0, 1.0) * 100.0


def upright_ratio(rpm, speed_ms, lat_g, crown=TYRE_CROWN):
    """Engine revs per km/h as it would read with the bike upright. Leaned over, the rear tyre rolls
    on a smaller radius, so the same gear shows more revs per km/h (8 to 9% more at 45 degrees);
    without this correction a leaned-over gear reads as the gear below. crown is how strong the
    effect is: the tyre's crown radius over the wheel's radius."""
    lean = np.arctan(np.abs(np.asarray(lat_g, float)))
    return np.asarray(rpm, float) / (np.asarray(speed_ms, float) * 3.6) * (1.0 - crown * (1.0 - np.cos(lean)))


def _in_gear_ratio(sess, lag, crown):
    """log(upright revs per km/h) for one session with the revs read `lag` seconds away from the GPS
    speed; where it is valid (ok) and where it is steady enough to be in gear with the clutch out."""
    t, v, lat, t_rpm, rpm_raw = sess
    rpm = np.interp(t + lag, t_rpm, rpm_raw)
    ok = (v * 3.6 > GEAR_MIN_KPH) & (rpm > GEAR_MIN_RPM) & np.isfinite(rpm)
    lr = np.zeros(len(t))
    lr[ok] = np.log(upright_ratio(rpm[ok], v[ok], lat[ok], crown))
    win = max(3, int(round(0.5 / float(np.median(np.diff(t))))) | 1)
    box = np.ones(win) / win
    calm = np.convolve(lr, box, mode="same")        # half-second average: rev counters step in coarse units
    whole = np.convolve(ok.astype(float), box, mode="same") > 0.999
    return calm, ok, ok & whole & (np.abs(np.gradient(calm, t)) < 0.04)      # steady: under 4% a second


def gears_from_ratios(sessions, gear_ratios):
    """Gear from engine revs and GPS speed, for logs with no gear channel, given the bike's gearbox
    ratios (lowest gear first). sessions: one (t, speed m/s, cornering g, revs' own times, revs)
    per session of the day.

    In gear g the revs per km/h are scale x gear_ratios[g], where the one unknown, scale, holds
    the primary drive, the sprockets and the tyre. It is fitted as the value that puts the most
    steady, in-gear samples on a gear. Because the steps between gears are all different, only one
    scale lines the samples up, so the gears come out with their real numbers even on a day when
    1st or 6th was never used. Two things about the day are fitted with it, from a few set values
    each: how far the GPS speed trails the ECU's revs (GEAR_LAGS), and how much the tyre's rolling
    radius shrinks with lean (GEAR_CROWNS). Without them a third of the samples on real logs
    fall between gears.

    Returns {"rpm_per_kph": per gear, "gears": a gear number per sample for each session (0 where
    none fits, as in a shift or with the clutch in), "fit": the share of steady samples on a gear,
    "lag_s", "tyre_crown"}, or None when the revs do not fit this gearbox: under GEAR_FIT_MIN of
    the steady samples on a gear, or another line-up (every gear read one higher or lower) within
    GEAR_FIT_MARGIN of it."""
    from scipy.signal import medfilt
    ref = np.log(np.asarray(gear_ratios, float))
    if len(ref) < 2 or not sessions:
        return None
    edges = np.arange(np.log(10.0), np.log(800.0), 0.002)
    scales = np.arange(edges[0] - ref.min() + GEAR_FIT_TOL, edges[-1] - ref.max() - GEAR_FIT_TOL, 0.002)
    centre_bin = np.round((scales[:, None] + ref[None, :] - edges[0]) / 0.002).astype(int)

    def on_gear(cum, n, tol):
        """For every scale: the share of the samples within tol of a gear."""
        w = int(round(tol / 0.002))
        lo, hi = np.clip(centre_bin - w, 0, len(cum) - 1), np.clip(centre_bin + w + 1, 0, len(cum) - 1)
        return (cum[hi] - cum[lo]).sum(axis=1) / n

    half = int(round(GEAR_FIT_TOL / 0.002))
    peak = 1.0 - np.abs(np.arange(-half, half + 1)) / (half + 1.0)      # 1 on the gear, falling to 0 at the tolerance
    best = None
    for lag in GEAR_LAGS:
        for crown in GEAR_CROWNS:
            parts = [_in_gear_ratio(s, lag, crown) for s in sessions]
            x = np.concatenate([calm[steady] for calm, _, steady in parts])
            if len(x) < 300:
                continue
            hist = np.histogram(x, edges)[0]
            cum = np.concatenate([[0], np.cumsum(hist)])
            # the lag and the tyre figure are the pair that gathers the samples most tightly on the gears:
            # each sample counts for more the nearer it sits to a gear, so a spread-out fit loses to a sharp one
            near = np.convolve(hist, peak, mode="same")
            tight = float(near[np.clip(centre_bin, 0, len(near) - 1)].sum(axis=1).max()) / len(x)
            if best is None or tight > best["tight"]:
                best = {"tight": tight, "cum": cum, "n": len(x), "lag": lag, "crown": crown, "parts": parts}
    if best is None:
        return None
    score = on_gear(best["cum"], best["n"], GEAR_FIT_TOL)
    top = float(score.max())
    fit = float(np.mean(scales[score >= top - 0.01]))               # the middle of the scales that fit best
    # a different line-up of the gears (every gear read one up or down) is at least a gear step away
    elsewhere = np.abs(scales - fit) > 2 * GEAR_FIT_TOL + 0.02
    other = float(score[elsewhere].max()) if elsewhere.any() else 0.0
    if top < GEAR_FIT_MIN or other > top - GEAR_FIT_MARGIN:
        return None
    best.update(top=top, fit=fit)
    centres = best["fit"] + ref
    gears = []
    for (calm, ok, _), s in zip(best["parts"], sessions):
        gap = np.abs(calm[ok][:, None] - centres[None, :])
        gear = np.zeros(len(calm), int)
        gear[ok] = np.where(gap.min(axis=1) <= GEAR_TOL, gap.argmin(axis=1) + 1, 0)
        win = max(3, int(round(0.4 / float(np.median(np.diff(s[0]))))) | 1)
        gears.append(medfilt(gear.astype(float), win).astype(int))          # no flicker between samples
    return {"rpm_per_kph": [float(c) for c in np.exp(centres)], "gears": gears, "fit": best["top"],
            "lag_s": best["lag"], "tyre_crown": best["crown"]}


def throttle_pickup_m(d, throttle_pct, apex_d):
    """How far after the slowest point (metres; negative = before it) the throttle comes back on:
    the first place from 60 m before the apex where it is over THROTTLE_ON percent and stays over
    for the next 20 m. None if it never does in the stretch given."""
    d, th = np.asarray(d, float), np.asarray(throttle_pct, float)
    on = th > THROTTLE_ON
    for i in np.flatnonzero((d >= apex_d - 60.0) & on):
        ahead = (d >= d[i]) & (d <= d[i] + 20.0)
        if on[ahead].all():
            return float(d[i] - apex_d)
    return None


def coast_seconds(t, throttle_pct, brake_pct=None):
    """Seconds with the throttle shut and, when there is a brake channel, the brake off too."""
    t, th = np.asarray(t, float), np.asarray(throttle_pct, float)
    idle = th < THROTTLE_ON
    if brake_pct is not None:
        idle &= np.asarray(brake_pct, float) < BRAKE_ON
    return float(np.sum(np.diff(t)[idle[:-1]])) if len(t) > 1 else 0.0


def full_throttle_pct(throttle_pct):
    """Share of the samples (so of the time, at a steady sample rate) at full throttle."""
    th = np.asarray(throttle_pct, float)
    return float(100.0 * np.mean(th >= FULL_THROTTLE)) if len(th) else 0.0
