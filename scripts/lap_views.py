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
