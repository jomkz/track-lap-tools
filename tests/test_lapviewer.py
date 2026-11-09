"""The viewer's file handling. No lap data needed.

Run from the repository folder:  python3 -I -m unittest discover -s tests
"""
import http.server
import json
import os
import sys
import tempfile
import threading
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


def serve(reply):
    """A throwaway local web server whose every GET answers with reply = (status, body bytes).
    Returns (server, port); close it with server.shutdown() and server.server_close()."""
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(reply[0])
            self.send_header("Content-Length", str(len(reply[1])))
            self.end_headers()
            self.wfile.write(reply[1])

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


class PortInUse(unittest.TestCase):
    """The configured port may belong to another program, or to a viewer that is already running."""
    OTHER = (401, b"Unauthorized")                                             # some other program's web server
    VIEWER = (200, json.dumps({"state": "idle", "version": 3, "xrk_folder": "/laps"}).encode())

    def setUp(self):
        self.open = []

    def tearDown(self):
        for srv in self.open:
            srv.shutdown()
            srv.server_close()

    def start(self, reply):
        srv, port = serve(reply)
        self.open.append(srv)
        return port

    def test_tells_a_viewer_from_another_program(self):
        self.assertTrue(lapviewer.viewer_at(self.start(self.VIEWER)))
        self.assertFalse(lapviewer.viewer_at(self.start(self.OTHER)))
        self.assertFalse(lapviewer.viewer_at(self.start((200, b'{"some": "other json"}'))))
        self.assertFalse(lapviewer.viewer_at(self.start((200, b"<html>not json</html>"))))

    def test_nothing_listening(self):
        srv, port = serve(self.OTHER)
        srv.shutdown()
        srv.server_close()                              # the port is free again
        self.assertFalse(lapviewer.viewer_at(port))
        self.assertFalse(lapviewer.port_taken(port))

    def test_port_taken_by_any_program(self):
        self.assertTrue(lapviewer.port_taken(self.start(self.OTHER)))
        self.assertTrue(lapviewer.port_taken(self.start(self.VIEWER)))

    def test_another_program_on_the_port_moves_the_viewer_to_the_next_free_one(self):
        taken = self.start(self.OTHER)
        srv, port = lapviewer.open_server(taken, tries=20)
        try:
            self.assertIsNotNone(srv)
            self.assertGreater(port, taken)
            self.assertLess(port, taken + 20)
            self.assertEqual(srv.server_address[1], port)
        finally:
            if srv:
                srv.server_close()

    def test_a_running_viewer_is_reused_not_doubled(self):
        running = self.start(self.VIEWER)
        self.assertEqual(lapviewer.open_server(running), (None, running))

    def test_free_port_is_taken_as_asked(self):
        probe, port = serve(self.OTHER)
        probe.shutdown()
        probe.server_close()
        srv, got = lapviewer.open_server(port)
        try:
            self.assertEqual(got, port)
        finally:
            srv.server_close()

    def test_no_port_left(self):
        taken = self.start(self.OTHER)
        self.assertEqual(lapviewer.open_server(taken, tries=1), (None, None))


if __name__ == "__main__":
    unittest.main()
