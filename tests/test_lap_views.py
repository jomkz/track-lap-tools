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


class Trend(unittest.TestCase):
    def test_steady_fade_is_found(self):
        x = np.arange(1.5, 19.5, 1.5)               # a lap every minute and a half
        got = lv.robust_trend(x, 90.0 + 0.1 * x)
        self.assertAlmostEqual(got["s_per_min"], 0.1, places=6)
        self.assertAlmostEqual(got["at_0_s"], 90.0, places=6)
        self.assertEqual(got["n"], 12)

    def test_one_lap_in_traffic_does_not_tilt_it(self):
        x = np.arange(1.5, 19.5, 1.5)
        y = 90.0 + 0.1 * x
        y[-1] += 5.0                                # the last lap loses five seconds in traffic
        got = lv.robust_trend(x, y)
        self.assertAlmostEqual(got["s_per_min"], 0.1, delta=0.02)
        least_squares = float(np.polyfit(x, y, 1)[0])
        self.assertGreater(least_squares, 0.2)      # the plain fit reports more than double the real fade

    def test_flat_session_with_noise_reads_flat(self):
        rng = np.random.default_rng(7)
        x = np.arange(1.5, 31.5, 1.5)
        got = lv.robust_trend(x, 90.0 + rng.normal(0, 0.3, len(x)))
        self.assertLess(abs(got["s_per_min"]), 0.03)

    def test_too_few_laps_gives_no_trend(self):
        self.assertIsNone(lv.robust_trend([1.5, 3.0, 4.5, 6.0], [90.0, 90.5, 91.0, 91.5]))
        self.assertIsNotNone(lv.robust_trend([1.5, 3.0, 4.5, 6.0, 7.5], [90.0, 90.5, 91.0, 91.5, 92.0]))
        self.assertIsNone(lv.robust_trend([2.0] * 6, [90.0, 90.5, 91.0, 91.5, 92.0, 92.5]))    # no spread in x

    def test_one_step_is_not_reported_as_a_steep_fade(self):
        # pace steps up by 1 s once, two thirds of the way through: the line must stay between the levels
        x = np.arange(1.5, 19.5, 1.5)
        y = np.where(x > 12.5, 91.0, 90.0)
        got = lv.robust_trend(x, y)
        line = got["at_0_s"] + got["s_per_min"] * x
        self.assertTrue(np.all(line >= 89.9) and np.all(line <= 91.1))


class LateMinusEarly(unittest.TestCase):
    def test_per_section_difference(self):
        laps = [[10.0, 20.0], [10.0, 20.0], [10.2, 20.0], [10.4, 20.1], [10.6, 20.1], [10.6, 20.1]]
        got = lv.late_minus_early(laps, 2)
        np.testing.assert_allclose(got, [0.6, 0.1], atol=1e-9)
        self.assertAlmostEqual(float(got.sum()), 0.7)       # the same as the lap-time fade over those laps

    def test_needs_two_separate_groups(self):
        self.assertIsNone(lv.late_minus_early([[10.0], [10.1]], 0))
        self.assertIsNone(lv.late_minus_early([[10.0], [10.1], [10.2]], 2))     # the thirds would overlap
        np.testing.assert_allclose(lv.late_minus_early([[10.0], [10.1], [10.2]], 1), [0.2])


class Rules(unittest.TestCase):
    def setUp(self):
        import lap_rules
        self.r = lap_rules

    def test_clean_laps(self):
        self.assertEqual(self.r.clean_flags([90.0, 91.0, 92.0, 100.0, 90.5]), [True, True, True, False, True])
        self.assertEqual(self.r.clean_flags([]), [])
        self.assertEqual(self.r.clean_flags([95.0]), [True])
        # exactly on the limit still counts: 107% of the 91.0 median
        self.assertEqual(self.r.clean_flags([91.0, 91.0, 91.0 * 1.07]), [True, True, True])

    def test_fade_is_last_third_minus_first_third(self):
        self.assertIsNone(self.r.fade([90.0, 91.0]))
        self.assertAlmostEqual(self.r.fade([90.0, 95.0, 91.0]), 1.0)
        self.assertAlmostEqual(self.r.fade([90.0, 90.0, 93.0, 93.0, 91.0, 92.0, 92.0]), 2.0)     # thirds of two laps
        self.assertEqual([self.r.third(n) for n in (0, 2, 3, 5, 6, 12)], [0, 0, 1, 1, 2, 4])


class Lean(unittest.TestCase):
    def test_lean_from_cornering_force(self):
        # 30 m/s round a 90 m radius is 10 m/s2, 1.0197 g: just over 45 degrees
        self.assertAlmostEqual(float(lv.lean_deg(30.0 ** 2 / 90.0 / 9.80665)), 45.56, places=2)
        self.assertAlmostEqual(float(lv.lean_deg(0.0)), 0.0)
        self.assertAlmostEqual(float(lv.lean_deg(-1.0)), 45.0)      # left or right, the angle is the same
        self.assertAlmostEqual(float(lv.lean_deg(1.2)), 50.19, places=2)

    def test_held_peak_ignores_a_spike_and_keeps_a_plateau(self):
        x = np.r_[np.linspace(0, 1.0, 20), np.full(30, 1.0), np.linspace(1.0, 0, 20)]
        self.assertAlmostEqual(lv.held_peak(x, 12), 1.0)
        spiked = x.copy()
        spiked[35:38] = 2.5                                         # three samples of GPS glitch
        self.assertAlmostEqual(lv.held_peak(spiked, 12), 1.0)
        self.assertAlmostEqual(float(spiked.max()), 2.5)            # what a plain maximum would have said
        held = x.copy()
        held[30:45] = 1.3                                           # a real, held, deeper lean
        self.assertAlmostEqual(lv.held_peak(held, 12), 1.3)

    def test_held_peak_needs_enough_samples(self):
        self.assertTrue(np.isnan(lv.held_peak(np.ones(5), 12)))
        self.assertAlmostEqual(lv.held_peak(np.ones(12), 12), 1.0)
        # a level held one sample short of the window does not count
        x = np.zeros(40)
        x[10:21] = 1.0
        self.assertAlmostEqual(lv.held_peak(x, 12), 0.0)
        x[10:22] = 1.0
        self.assertAlmostEqual(lv.held_peak(x, 12), 1.0)

    def test_despike_removes_short_spikes_only(self):
        x = np.r_[np.zeros(20), np.full(20, 1.0), np.zeros(20)]
        spiked = x.copy()
        spiked[5] = 9.0
        spiked[30:32] = -9.0
        np.testing.assert_allclose(lv.despike(spiked), x)


class GripCircle(unittest.TestCase):
    def circle(self, radius, n=3600):
        th = np.linspace(0, 2 * np.pi, n, endpoint=False)
        return radius * np.cos(th), radius * np.sin(th)

    def round_env(self, radius=1.0):
        return lv.envelope([self.circle(radius)] * 3, 5)

    def test_outline_of_a_circle_is_its_radius(self):
        env = self.round_env(1.1)
        self.assertEqual(len(env), 24)
        np.testing.assert_allclose(env, 1.1, atol=1e-6)

    def test_outline_follows_the_shape(self):
        # plenty of cornering grip, hard braking, little acceleration: the outline is not round
        rng = np.random.default_rng(3)
        laps = []
        for _ in range(10):
            th = rng.uniform(0, 2 * np.pi, 4000)
            limit = np.where(np.sin(th) > 0.5, 0.4, 1.0)            # weak in the acceleration direction
            r = limit * rng.uniform(0, 1, len(th))
            laps.append((r * np.cos(th), r * np.sin(th)))
        env = lv.envelope(laps, 5)
        self.assertAlmostEqual(env[0], 0.99, delta=0.02)            # 0 deg: right-hand cornering
        self.assertAlmostEqual(env[6], 0.40, delta=0.02)            # 90 deg: acceleration
        self.assertAlmostEqual(env[18], 0.99, delta=0.02)           # 270 deg: braking

    def test_directions_with_no_samples_are_filled_from_their_neighbours(self):
        th = np.radians(np.r_[np.full(100, 0.0), np.full(100, 180.0)])      # samples in two directions only
        env = lv.envelope([(np.cos(th), np.sin(th))], 5)
        self.assertFalse(np.isnan(env).any())
        np.testing.assert_allclose(env, 1.0, atol=1e-9)
        self.assertTrue(np.isnan(lv.envelope([(np.zeros(10), np.ones(10))], 5)).all())      # too few anywhere
        self.assertTrue(np.isnan(lv.envelope([], 5)).all())

    def test_one_wild_sample_does_not_set_the_outline(self):
        lat, lon = self.circle(1.0)
        lat = lat.copy()
        lat[0] = 4.0
        self.assertLess(lv.envelope([(lat, lon)], 5)[0], 1.01)

    def test_one_bad_lap_does_not_set_the_outline(self):
        good, bad = self.circle(1.0), self.circle(2.0)             # a whole lap of doubled readings
        env = lv.envelope([good] * 9 + [bad], 5)
        np.testing.assert_allclose(env, 1.0, atol=1e-6)

    def test_more_laps_do_not_push_the_outline_out(self):
        rng = np.random.default_rng(11)
        def lap():
            th = np.linspace(0, 2 * np.pi, 2400, endpoint=False)
            r = 1.0 + rng.normal(0, 0.05, len(th))                  # the same rider, with noise
            return r * np.cos(th), r * np.sin(th)
        few, many = lv.envelope([lap() for _ in range(5)], 5), lv.envelope([lap() for _ in range(60)], 5)
        self.assertLess(abs(float(np.mean(many) - np.mean(few))), 0.02)
        pooled_max = max(float(np.hypot(*lap()).max()) for _ in range(60))
        self.assertGreater(pooled_max, float(np.mean(many)) + 0.05)     # what the day's largest sample would say

    def test_coasting_does_not_lower_the_outline(self):
        # hard straight-line braking for 2 s of the lap, then ten minutes of coasting in the same direction
        lon = np.r_[np.full(50, -0.9), np.full(15000, -0.05)]
        env = lv.envelope([(np.zeros(len(lon)), lon)], 5)
        self.assertAlmostEqual(env[18], 0.9, delta=0.01)
        self.assertLess(float(np.percentile(np.abs(lon), 98)), 0.1)     # what a percentile of samples would have said

    def test_limit_share(self):
        env = self.round_env(1.0)
        lat, lon = self.circle(1.0)
        np.testing.assert_allclose(lv.limit_share(0.5 * lat, 0.5 * lon, env), 0.5, atol=1e-6)
        np.testing.assert_allclose(lv.limit_share(np.array([0.0]), np.array([-0.8]), env), 0.8, atol=1e-6)

    def corner(self, blend):
        """Brake at 0.9 g, then lean to 1.0 g. blend=True trades one for the other round the circle;
        blend=False lets the brake go first, then leans."""
        n = 40
        if blend:
            th = np.linspace(-np.pi / 2, 0, n)
            lat, lon = np.cos(th), 0.9 * np.sin(th)
        else:
            lat = np.r_[np.zeros(n // 2), np.linspace(0, 1.0, n // 2)]
            lon = np.r_[np.linspace(-0.9, 0, n // 2), np.zeros(n // 2)]
        return np.r_[lat, np.full(20, 1.0)], np.r_[lon, np.zeros(20)]

    def test_trail_braking_shows_as_a_high_entry_blend(self):
        env = self.round_env()
        blended = lv.corner_grip(*self.corner(True), env, 0.04)
        released = lv.corner_grip(*self.corner(False), env, 0.04)
        self.assertGreater(blended["entry_blend_pct"], 85)
        self.assertLess(released["entry_blend_pct"], 15)            # passed through no braking and no lean
        self.assertGreater(blended["near_limit_pct"], released["near_limit_pct"])
        self.assertAlmostEqual(blended["peak_brake_g"], 0.9, delta=0.01)
        self.assertAlmostEqual(blended["peak_lat_g"], 1.0, delta=0.01)

    def test_corner_with_no_brake_zone_has_no_entry_blend(self):
        env = self.round_env()
        got = lv.corner_grip(np.r_[np.linspace(0, 1, 20), np.ones(20)], np.full(40, -0.1), env, 0.04)
        self.assertIsNone(got["entry_blend_pct"])
        self.assertIsNone(lv.corner_grip(np.ones(3), np.zeros(3), env, 0.04))      # too few samples


class Gyro(unittest.TestCase):
    def test_tilted_gyro_that_follows_the_lean_agrees(self):
        rng = np.random.default_rng(5)
        t = np.arange(0, 120, 0.04)
        lean_rate = 20 * np.sin(t / 3.0) + 8 * np.sin(t / 0.7)
        yaw_of_track = 15 * np.cos(t / 5.0)
        tilt = np.radians(42)                                       # the logger sits tilted on the bike
        roll = lean_rate * np.cos(tilt) + yaw_of_track * np.sin(tilt) + rng.normal(0, 1, len(t)) + 1.5
        yaw = -lean_rate * np.sin(tilt) + yaw_of_track * np.cos(tilt) + rng.normal(0, 1, len(t))
        got = lv.gyro_agreement(lean_rate, roll, yaw, rng.normal(0, 1, len(t)))
        self.assertGreater(got["r"], 0.97)
        self.assertEqual(got["n"], len(t))

    def test_unrelated_gyro_does_not_agree(self):
        rng = np.random.default_rng(6)
        t = np.arange(0, 120, 0.04)
        got = lv.gyro_agreement(20 * np.sin(t / 3.0), *(rng.normal(0, 5, len(t)) for _ in range(3)))
        self.assertLess(got["r"], 0.1)

    def test_too_little_data(self):
        self.assertIsNone(lv.gyro_agreement(np.ones(100), np.ones(100), np.ones(100), np.ones(100)))
        self.assertIsNone(lv.gyro_agreement(np.zeros(1000), np.zeros(1000), np.zeros(1000), np.zeros(1000)))


if __name__ == "__main__":
    unittest.main()
