"""The viewer's extra views on made-up laps whose answers are known. No lap data needed.

Run from the repository folder:  python3 -I -m unittest discover -s tests
"""
import os
import sys
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
import lap_views as lv  # noqa: E402

L = 1000.0      # lap length, metres


def curve(speeds, t0=100.0, hz=25.0, pad_m=150.0):
    """A lap ridden at a fixed speed in each stretch. speeds: (end distance, m/s) in order.
    Sampled at `hz` like the logger, from pad_m before the line to pad_m after the lap."""
    def time_to(d):
        t, at = 0.0, 0.0
        for end, v in speeds:
            if d <= end:
                return t + (d - at) / v
            t += (end - at) / v
            at = end
        return t + (d - at) / speeds[-1][1]
    lap_time = time_to(L)
    ts = np.arange(-pad_m / speeds[0][1], lap_time + pad_m / speeds[-1][1], 1.0 / hz)
    edges = np.linspace(-pad_m, L + pad_m, 4001)
    clock = np.array([time_to(d) if d >= 0 else d / speeds[0][1] for d in edges])
    return np.interp(ts, clock, edges), ts + t0


STEADY = curve([(L, 50.0)])                     # 20.000 s
SLOW_FAST = curve([(500.0, 40.0), (L, 60.0)])   # 12.5 + 8.333 = 20.833 s
TOL = 0.005     # seconds: at 25 Hz a change of speed falls between two samples, as in a real log


class Clock(unittest.TestCase):
    def test_grid_ends_on_the_line(self):
        g = lv.lap_grid(1005.0)
        self.assertEqual((g[0], g[1], g[-2], g[-1]), (0.0, 10.0, 1000.0, 1005.0))
        self.assertEqual(lv.lap_grid(1000.0)[-1], 1000.0)
        self.assertEqual(len(lv.lap_grid(1000.0)), 101)

    def test_clock_starts_at_the_line_whatever_the_time_of_day(self):
        for t0 in (0.0, 100.0, 5000.0):
            c = curve([(L, 50.0)], t0=t0)
            self.assertAlmostEqual(float(lv.clock_at(c, 0.0)), 0.0, places=6)
            self.assertAlmostEqual(float(lv.clock_at(c, 500.0)), 10.0, delta=TOL)
            self.assertAlmostEqual(float(lv.clock_at(c, L)), 20.0, delta=TOL)

    def test_delta_rises_where_time_is_lost_and_falls_where_it_is_gained(self):
        g = lv.lap_grid(L)
        d = lv.delta_cs(lv.clock_at(SLOW_FAST, g), lv.clock_at(STEADY, g))
        self.assertEqual(d[0], 0)
        self.assertAlmostEqual(d[50], 250, delta=1)      # 500 m: 12.5 s against 10.0 s
        self.assertAlmostEqual(d[-1], 83, delta=1)       # the lap-time difference, 0.833 s
        self.assertTrue(all(b >= a for a, b in zip(d[:51], d[1:51])))       # losing all the way to 500 m
        self.assertTrue(all(b <= a for a, b in zip(d[50:], d[51:])))        # gaining from there
        self.assertEqual(set(lv.delta_cs(lv.clock_at(STEADY, g), lv.clock_at(STEADY, g))), {0})


class Pieces(unittest.TestCase):
    def test_wrapping_section_is_split_at_the_line(self):
        got = lv.pieces_in_order([(200.0, 800.0, False, "b"), (800.0, 200.0, True, "a")], L)
        self.assertEqual(got, [(0.0, 200.0, "a"), (200.0, 800.0, "b"), (800.0, L, "a")])

    def test_a_gap_or_an_overlap_is_refused(self):
        with self.assertRaisesRegex(ValueError, "missing"):
            lv.pieces_in_order([(0.0, 400.0, False, "a"), (500.0, L, False, "b")], L)
        with self.assertRaisesRegex(ValueError, "covered twice"):
            lv.pieces_in_order([(0.0, 600.0, False, "a"), (500.0, L, False, "b")], L)
        with self.assertRaisesRegex(ValueError, "end at"):
            lv.pieces_in_order([(0.0, 400.0, False, "a")], L)


class IdealLap(unittest.TestCase):
    def test_ideal_takes_each_piece_from_the_faster_lap(self):
        g = lv.lap_grid(L)
        curves = {"steady": STEADY, "slowfast": SLOW_FAST}
        pieces = lv.pieces_in_order([(0.0, 500.0, False, "steady"), (500.0, L, False, "slowfast")], L)
        clock, total = lv.composite_clock(g, pieces, curves)
        self.assertAlmostEqual(total, 10.0 + 500.0 / 60.0, delta=TOL)       # 18.333 s
        d = lv.delta_cs(clock, lv.clock_at(STEADY, g))
        self.assertEqual(d[50], 0)                      # level to 500 m: it is the same lap
        self.assertAlmostEqual(d[-1], -167, delta=1)     # then 1.667 s gained
        self.assertAlmostEqual(float(clock[-1]), total, places=6)

    def test_ideal_with_a_section_through_the_line(self):
        g = lv.lap_grid(L)
        curves = {"steady": STEADY, "slowfast": SLOW_FAST}
        # the section through the line (800 m round to 200 m) from the steady lap, the middle from the other
        pieces = lv.pieces_in_order([(800.0, 200.0, True, "steady"), (200.0, 800.0, False, "slowfast")], L)
        clock, total = lv.composite_clock(g, pieces, curves)
        want = 200 / 50 + (300 / 40 + 300 / 60) + 200 / 50          # 4 + 12.5 + 4 = 20.5 s
        self.assertAlmostEqual(total, want, delta=TOL)
        self.assertAlmostEqual(float(clock[20]), 4.0, delta=TOL)    # 200 m
        self.assertAlmostEqual(float(clock[80]), 16.5, delta=TOL)   # 800 m
        self.assertTrue(np.all(np.diff(clock) > 0))                 # a clock never runs backwards

    def test_a_lap_stitched_from_itself_is_itself(self):
        g = lv.lap_grid(L)
        pieces = lv.pieces_in_order([(700.0, 300.0, True, "x"), (300.0, 700.0, False, "x")], L)
        clock, total = lv.composite_clock(g, pieces, {"x": SLOW_FAST})
        np.testing.assert_allclose(clock, lv.clock_at(SLOW_FAST, g), atol=1e-6)
        self.assertAlmostEqual(total, 12.5 + 500 / 60, delta=TOL)


if __name__ == "__main__":
    unittest.main()
