"""The viewer's file handling. No lap data needed.

Run from the repository folder:  python3 -I -m unittest discover -s tests
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "viewer"))
import lapviewer  # noqa: E402


class CompressedCopies(unittest.TestCase):
    def test_xrz_next_to_its_xrk_is_dropped(self):
        got = lapviewer.drop_compressed_copies(["/d/a_0001.xrk", "/d/a_0001.xrz", "/d/a_0002.XRZ", "/d/A_0003.XRK",
                                                "/d/a_0003.xrz", "/other/a_0001.xrz"])
        self.assertEqual(got, ["/d/a_0001.xrk", "/d/a_0002.XRZ", "/d/A_0003.XRK", "/other/a_0001.xrz"])

    def test_nothing_to_drop(self):
        self.assertEqual(lapviewer.drop_compressed_copies([]), [])
        self.assertEqual(lapviewer.drop_compressed_copies(["/d/a.xrz"]), ["/d/a.xrz"])


class ToolsSignature(unittest.TestCase):
    def test_changes_when_a_track_file_changes(self):
        with tempfile.TemporaryDirectory() as d:
            scripts, tracks = os.path.join(d, "scripts"), os.path.join(d, "tracks", "some-track")
            os.makedirs(scripts)
            os.makedirs(tracks)
            open(os.path.join(scripts, "tool.py"), "w").write("x = 1\n")
            open(os.path.join(tracks, "turns.csv"), "w").write("turn,name,lat,lon\n1,,30.0,-97.0\n")
            bikes = os.path.join(d, "bikes")
            os.makedirs(bikes)
            open(os.path.join(bikes, "some-bike.json"), "w").write('{"name": "Some bike"}')
            keep = lapviewer.SCRIPTS, lapviewer.TRACKS, lapviewer.BIKES
            lapviewer.SCRIPTS, lapviewer.TRACKS, lapviewer.BIKES = scripts, os.path.join(d, "tracks"), bikes
            try:
                first = lapviewer.tools_sig()
                self.assertEqual(first, lapviewer.tools_sig())
                open(os.path.join(tracks, "turns.csv"), "a").write("2,Hairpin,30.001,-97.001\n")
                second = lapviewer.tools_sig()
                self.assertNotEqual(first, second)
                open(os.path.join(scripts, "tool.py"), "w").write("x = 2\n")
                third = lapviewer.tools_sig()
                self.assertNotEqual(second, third)
                open(os.path.join(bikes, "some-bike.json"), "w").write('{"name": "Some bike", "gear_ratios": [2.8, 2.1]}')
                self.assertNotEqual(third, lapviewer.tools_sig())
            finally:
                lapviewer.SCRIPTS, lapviewer.TRACKS, lapviewer.BIKES = keep


class ChicaneSummary(unittest.TestCase):
    def test_name_and_numbers_from_the_report(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "chicane.md")
            open(p, "w").write("# Turn 6 chicane cost, 11/06/2026 (Bike, CW)\n\n- Estimated cost per lap: about 0.7 s "
                               "(range 0.55 to 0.70 s over the model cases, median of 29 laps).\n")
            self.assertEqual(lapviewer.parse_chicane_md(p),
                             {"name": "Turn 6 chicane", "per_lap_s": 0.7, "low_s": 0.55, "high_s": 0.70})
            self.assertIsNone(lapviewer.parse_chicane_md(os.path.join(d, "missing.md")))


if __name__ == "__main__":
    unittest.main()
