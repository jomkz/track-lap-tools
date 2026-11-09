"""Shared lap helpers on made-up laps: placing sessions on a track, direction, GPS dropouts.
No lap data needed.

Run from the repository folder:  python3 -I -m unittest discover -s tests
"""
import math
import os
import sys
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
import track_common as tc  # noqa: E402

HARRIS = (29.9180, -97.8720)
ELSEWHERE = (39.3600, -75.0700)     # far north of Harris Hill: a fixed Texas map origin would squash it


def oval(lat0, lon0, clockwise=False, lap_s=60.0, hz=25.0, laps=2, hole=None, venue=""):
    """A made-up session: an out lap, `laps` full laps and an in lap round a 300 m x 150 m oval
    at constant angular speed. hole=(lap number, start s, end s) drops that lap's GPS samples."""
    n = int(lap_s * hz)
    t = np.arange((laps + 2) * n) / hz
    th = 2 * math.pi * t / lap_s * (-1 if clockwise else 1)
    east, north = 300.0 * np.cos(th), 150.0 * np.sin(th)
    lat = lat0 + np.degrees(north / 6371000.0)
    lon = lon0 + np.degrees(east / (6371000.0 * math.cos(math.radians(lat0))))
    marks = [{"num": k, "lap_type": "out" if k == 0 else "in" if k == laps + 1 else "full",
              "start_time": k * lap_s * 1000, "end_time": (k + 1) * lap_s * 1000} for k in range(laps + 2)]
    keep = np.ones(len(t), bool)
    if hole:
        k, a, b = hole
        keep &= ~((t >= k * lap_s + a) & (t < k * lap_s + b))
    return {"name": "a_0001", "t": t[keep], "lat": lat[keep], "lon": lon[keep], "laps": marks,
            "vehicle": "Test bike", "date": "01/01/2026", "venue": venue}


class Placing(unittest.TestCase):
    def test_known_track_by_gps_and_unknown_by_logger_name(self):
        s = oval(*HARRIS, venue="H2R")
        self.assertEqual(tc.place([s])["id"], "harris-hill")
        s = oval(*ELSEWHERE, venue="Some Raceway")
        track = tc.place([s])
        self.assertEqual((track["known"], track["name"]), (False, "Some Raceway"))
        self.assertIs(s["track"], track)

    def test_map_is_true_to_scale_away_from_harris_hill(self):
        # the oval is 600 m wide wherever it is; a map origin fixed in Texas would give about 535 m here
        for where in (HARRIS, ELSEWHERE):
            s = oval(*where)
            tc.place([s])
            self.assertAlmostEqual(s["x"].max() - s["x"].min(), 600.0, delta=1.0)
            self.assertAlmostEqual(s["y"].max() - s["y"].min(), 300.0, delta=1.0)

    def test_sessions_placed_together_share_one_origin(self):
        a, b = oval(*ELSEWHERE), oval(ELSEWHERE[0] + 0.0005, ELSEWHERE[1])    # b sits 56 m north
        track = tc.place([a, b])
        self.assertIs(a["track"], b["track"])
        self.assertAlmostEqual(float(np.mean(b["y"]) - np.mean(a["y"])), 55.6, delta=0.5)
        self.assertAlmostEqual(track["lat0"], ELSEWHERE[0] + 0.00025, places=4)


class Laps(unittest.TestCase):
    def test_direction_from_the_path(self):
        for clockwise, want in ((False, "CCW"), (True, "CW")):
            s = oval(*ELSEWHERE, clockwise=clockwise)
            tc.place([s])
            laps = tc.full_laps([s])
            self.assertEqual([l["num"] for l in laps], [1, 2])      # out and in laps never count
            self.assertEqual({l["dir"] for l in laps}, {want})
            self.assertAlmostEqual(laps[0]["time"], 60.0)

    def test_gps_gap(self):
        t = np.array([10.0, 10.04, 10.08, 15.0, 15.04])
        self.assertAlmostEqual(tc.gps_gap(t, 10.0, 15.04), 4.92)
        self.assertAlmostEqual(tc.gps_gap(t[:3], 10.0, 20.0), 9.92)     # samples stop before the lap ends
        self.assertAlmostEqual(tc.gps_gap(np.array([]), 10.0, 20.0), 10.0)

    def test_lap_with_a_gps_dropout_is_left_out(self):
        s = oval(*ELSEWHERE, laps=3, hole=(2, 48.0, 60.0))      # lap 2 loses its last 12 s
        tc.place([s])
        self.assertEqual([l["num"] for l in tc.full_laps([s])], [1, 3])
        kept = tc.full_laps([s], keep_gaps=True)
        self.assertEqual([l["num"] for l in kept], [1, 2, 3])
        self.assertAlmostEqual(kept[1]["gap"], 12.0, delta=0.1)
        self.assertLess(kept[0]["gap"], 0.1)

    def test_short_dropout_is_kept(self):
        s = oval(*ELSEWHERE, hole=(1, 20.0, 20.5))      # half a second: under GPS_GAP_S
        tc.place([s])
        self.assertEqual([l["num"] for l in tc.full_laps([s])], [1, 2])


class Reference(unittest.TestCase):
    def setUp(self):
        self.s = oval(*ELSEWHERE)
        tc.place([self.s])
        self.laps = tc.full_laps([self.s])
        self.ref = tc.Ref(self.laps[0])

    def test_lap_length(self):
        # Ramanujan's perimeter for semi-axes 300 and 150 m
        a, b = 300.0, 150.0
        want = math.pi * (3 * (a + b) - math.sqrt((3 * a + b) * (a + 3 * b)))
        self.assertAlmostEqual(self.ref.L, want, delta=1.0)

    def test_projecting_a_shifted_lap_finds_the_shift(self):
        other = oval(ELSEWHERE[0] + 0.00005, ELSEWHERE[1])      # the same lap with the GPS 5.6 m north
        tc.place([other], self.s["track"])
        lap = tc.full_laps([other])[0]
        d, off, shift = self.ref.project(lap)
        self.assertAlmostEqual(shift, 5.56, delta=0.3)
        self.assertLess(float(off.max()), 1.0)
        self.assertGreater(float(d.max()), 0.95 * self.ref.L)

    def test_turns_come_from_the_table_given(self):
        self.assertEqual(tc.turns(self.ref), [])        # unknown track, no table, no analysed day
        self.assertEqual(tc.turns(self.ref, out_dir="/no/such/folder"), [])


if __name__ == "__main__":
    unittest.main()
