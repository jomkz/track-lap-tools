"""Track definitions and track matching. No lap data needed.

Run from the repository folder:  python3 -I -m unittest discover -s tests
"""
import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
sys.path.insert(0, SCRIPTS)
import tracks  # noqa: E402

HARRIS = (29.9185, -97.8725)        # inside Harris Hill Road
ELSEWHERE = (33.3690, -97.4320)     # a circuit with no folder in tracks/


class KnownTracks(unittest.TestCase):
    def test_every_track_folder_loads(self):
        for t in tracks.known():
            self.assertTrue(t["name"], t["id"])
            self.assertGreater(t["radius_m"], 0, t["id"])
            nums = [r["turn"] for r in tracks.turn_table(t)]
            self.assertEqual(len(nums), len(set(nums)), f"{t['id']}: a turn number is listed twice")
            for c in t["chicanes"]:
                self.assertIn(c["direction"], ("CW", "CCW"), t["id"])
                for key in ("turn", "after_turn", "before_turn"):
                    self.assertIn(c[key], nums, f"{t['id']}: chicane {key} is not in the turn table")
                self.assertTrue(os.path.exists(os.path.join(t["dir"], c["natural_radius"])), t["id"])

    def test_harris_hill_is_found_by_gps(self):
        t = tracks.match(*HARRIS)
        self.assertEqual(t["id"], "harris-hill")
        self.assertTrue(t["known"])
        turns = tracks.turn_table(t)
        self.assertEqual(sorted(r["turn"] for r in turns), [1, 2, 4, 5, 6, 7, 8, 9, 10, 11])
        self.assertEqual(next(r["label"] for r in turns if r["turn"] == 10), "T10 The Hairpin")
        self.assertEqual(next(r["label"] for r in turns if r["turn"] == 1), "T1")
        self.assertEqual(t["logger_track_names"], {"R2H": "CCW", "H2R": "CW"})

    def test_chicane_only_in_its_direction(self):
        t = tracks.match(*HARRIS)
        self.assertEqual(tracks.chicane(t, "CW")["turn"], 6)
        self.assertIsNone(tracks.chicane(t, "CCW"))

    def test_logger_track_name_does_not_pick_the_track(self):
        # the logger was left on a Harris Hill setting at another circuit: GPS decides
        t = tracks.find(*ELSEWHERE, venue="R2H")
        self.assertFalse(t["known"])
        self.assertEqual(t["name"], "R2H")
        self.assertEqual(t["logger_track_names"], {})


class UnknownTracks(unittest.TestCase):
    def test_no_match_far_away(self):
        self.assertIsNone(tracks.match(*ELSEWHERE))

    def test_unknown_track_takes_the_logger_name_and_its_own_origin(self):
        t = tracks.find(*ELSEWHERE, venue="Some Raceway")
        self.assertEqual((t["id"], t["name"], t["known"]), ("some-raceway", "Some Raceway", False))
        self.assertEqual((t["lat0"], t["lon0"]), ELSEWHERE)
        self.assertEqual(tracks.turn_table(t), [])
        self.assertEqual(t["chicanes"], [])

    def test_unknown_track_with_no_logger_name(self):
        t = tracks.find(*ELSEWHERE)
        self.assertEqual((t["id"], t["name"]), ("unknown-track", "Unknown track"))

    def test_just_outside_the_radius_is_not_the_track(self):
        t = tracks.match(*HARRIS)
        north = t["lat0"] + (t["radius_m"] + 200) / 111_195.0
        self.assertIsNone(tracks.match(north, t["lon0"]))
        self.assertEqual(tracks.match(t["lat0"] + (t["radius_m"] - 200) / 111_195.0, t["lon0"])["id"], "harris-hill")


class Helpers(unittest.TestCase):
    def test_distance(self):
        self.assertAlmostEqual(tracks.distance_m(30.0, -97.0, 31.0, -97.0), 111_195, delta=50)
        self.assertEqual(tracks.distance_m(30.0, -97.0, 30.0, -97.0), 0.0)

    def test_slug(self):
        self.assertEqual(tracks.slug("Eagles Canyon Raceway (2.5 mi)"), "eagles-canyon-raceway-2-5-mi")
        self.assertEqual(tracks.slug(None), "")

    def test_read_turns_missing_file(self):
        self.assertEqual(tracks.read_turns(""), [])
        self.assertEqual(tracks.read_turns("/no/such/file.csv"), [])

    def test_read_turns_from_a_days_corners_csv(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "corners.csv")
            with open(p, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["corner", "turn", "apex_m", "apex_lat", "apex_lon", "peak_lat_g", "min_kph_best_lap"])
                w.writerow(["T1", "R", 292, 33.368813, -97.431006, 1.11, 81.5])
                w.writerow(["T10 The Hairpin", "L", 361, 33.369413, -97.430889, 1.16, 93.6])
            rows = tracks.read_turns(p)
        self.assertEqual([(r["turn"], r["name"], r["label"]) for r in rows],
                         [(1, "", "T1"), (10, "The Hairpin", "T10 The Hairpin")])
        self.assertAlmostEqual(rows[0]["lat"], 33.368813)

    def test_bad_track_file_says_which_file(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "broken"))
            with open(os.path.join(d, "broken", "track.json"), "w") as f:
                f.write('{"name": "No centre"}')
            with self.assertRaises(SystemExit) as e:
                tracks.known(d)
            self.assertIn(os.path.join("broken", "track.json"), str(e.exception))


class TrackAdd(unittest.TestCase):
    """scripts/track_add.py on a made-up analysed day."""

    def run_add(self, tracks_dir, analysis, *extra):
        return subprocess.run([sys.executable, "-I", os.path.join(SCRIPTS, "track_add.py"), "Some Raceway", analysis,
                               "--tracks-dir", tracks_dir, *extra], capture_output=True, text=True)

    def make_day(self, d, lat0, lon0):
        analysis = os.path.join(d, "analysis_2026-01-01")
        os.makedirs(analysis)
        lat = [lat0 + 0.004 * k / 10 for k in range(11)]
        lon = [lon0 + 0.006 * k / 10 for k in range(11)]
        with open(os.path.join(analysis, "viewer.json"), "w") as f:
            json.dump({"direction": "CCW", "path": {"lat": lat, "lon": lon}}, f)
        with open(os.path.join(analysis, "corners.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["corner", "turn", "apex_m", "apex_lat", "apex_lon", "peak_lat_g", "min_kph_best_lap"])
            w.writerow(["T1", "R", 100, lat[2], lon[2], 1.1, 80.0])
            w.writerow(["T2", "L", 300, lat[7], lon[7], 1.0, 90.0])
        return analysis

    def test_writes_a_track_that_then_matches(self):
        with tempfile.TemporaryDirectory() as d:
            tdir = os.path.join(d, "tracks")
            analysis = self.make_day(d, *ELSEWHERE)
            r = self.run_add(tdir, analysis, "--place", "Somewhere, Texas")
            self.assertEqual(r.returncode, 0, r.stderr)
            t = tracks.match(ELSEWHERE[0] + 0.002, ELSEWHERE[1] + 0.003, tdir)
            self.assertEqual((t["id"], t["name"], t["place"]), ("some-raceway", "Some Raceway", "Somewhere, Texas"))
            self.assertEqual([r["label"] for r in tracks.turn_table(t)], ["T1", "T2"])
            self.assertIn("counterclockwise", t["turns_note"])
            # the whole path is inside the radius, with room to spare
            self.assertGreater(t["radius_m"], tracks.distance_m(t["lat0"], t["lon0"], *ELSEWHERE) + 900)
            # a second run must not overwrite it
            again = self.run_add(tdir, analysis)
            self.assertNotEqual(again.returncode, 0)
            self.assertIn("already at a known track", again.stderr)

    def test_refuses_a_day_with_no_analysis(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.run_add(os.path.join(d, "tracks"), os.path.join(d, "nothing"))
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("run scripts/run_day.py", r.stderr)
            self.assertFalse(os.path.exists(os.path.join(d, "tracks")))


if __name__ == "__main__":
    unittest.main()
