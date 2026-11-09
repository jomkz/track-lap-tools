#!/usr/bin/env python3
"""Pit lap viewer: a small local web app for AiM Solo DL laps, at any track.

Start it with start_windows.bat (Windows) or ./start_linux.sh (Fedora). It opens
http://127.0.0.1:8765 in the browser. No internet needed once it is installed.

What it does
    1. Watches a folder for .xrk / .xrz files (config.json "xrk_folder"; files can also be
       dropped onto the page, which saves them there).
    2. Groups files by the date the logger recorded and, for every new or changed date, runs the
       existing tools (../scripts/run_day.py) into its own data folder. The analysis is the same
       code as the cloud runs: lap table and direction, ideal lap, time lost by section, tire-wear
       signal, and the cost of a cone chicane where the track defines one. A day is analysed again
       when its files, the scripts or a track definition (../tracks/) change.
    3. Serves views of each day: session board, track map of time lost, corner compare, time delta,
       lean and grip, stint.
    4. Writes a small summary of each day and session (JSON and CSV, about 170 KB a day) into
       "summary_folder", which can be a folder synced to Google Drive.

Options
    --folder PATH   watch this folder instead of the one in config.json
    --port N        port (default from config.json, 8765)
    --no-browser    don't open the browser
    --once          process the folder once, write summaries, and exit (no web server)
"""
import argparse
import csv
import hashlib
import http.server
import json
import os
import re
import shutil
import socketserver
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)                      # the repository folder
SCRIPTS = os.path.join(TOOLS, "scripts")
TRACKS = os.path.join(TOOLS, "tracks")
STATIC = os.path.join(HERE, "static")
CONFIG_PATH = os.path.join(HERE, "config.json")

DEFAULTS = {
    "xrk_folder": "xrk",               # where .xrk files land (relative paths are inside viewer/)
    "summary_folder": "summaries",     # small per-day / per-session summaries (sync this one to Drive)
    "data_folder": "data",             # analysis cache, safe to delete
    "port": 8765,
    "scan_seconds": 15,                # how often to look for new files
    "ramp_targets_min": [15, 25, 30, 35],   # session ramp step 1 (session 1 is the warm-up)
    "ramp_label": "Ramp step 1",
    "speed_unit": "kmh",               # "kmh" or "mph" (display only; tools work in km/h)
    "open_browser": True,
}
XRK_EXT = (".xrk", ".xrz")


# ---------------------------------------------------------------- config

def load_config():
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG_PATH):
        try:
            cfg.update(json.load(open(CONFIG_PATH, encoding="utf-8")))
        except (OSError, ValueError) as e:
            print(f"config.json could not be read ({e}); using defaults")
    else:
        json.dump(DEFAULTS, open(CONFIG_PATH, "w", encoding="utf-8"), indent=2)
    for k in ("xrk_folder", "summary_folder", "data_folder"):
        p = os.path.expandvars(os.path.expanduser(str(cfg[k])))
        cfg[k] = p if os.path.isabs(p) else os.path.normpath(os.path.join(HERE, p))
        os.makedirs(cfg[k], exist_ok=True)
    return cfg


# ---------------------------------------------------------------- reading tool outputs

def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and abs(v) != float("inf") else None


def fmt_lap(sec):
    if sec is None:
        return ""
    m, s = divmod(sec, 60)
    return f"{int(m)}:{s:06.3f}"


def turn_nums(label):
    return [int(n) for n in re.findall(r"T(\d+)", label or "")]


def parse_tire_md(path):
    """Flagged turns from tire_check.md (lines '- T8 Dam Turn: slowest speed ...')."""
    flags = []
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            m = re.match(r"- (T\d+[^:]*): slowest speed (.*?) km/h, peak lateral g (.*)", line.strip())
            if m:
                flags.append({"turn": m.group(1), "kph": [float(x) for x in m.group(2).split(" -> ")],
                              "lat_g": [float(x) for x in m.group(3).split(" -> ")]})
    return flags


def parse_chicane_md(path):
    if not os.path.exists(path):
        return None
    txt = open(path, encoding="utf-8").read()
    m = re.search(r"about ([\d.]+) s \(range ([\d.]+) to ([\d.]+) s", txt)
    if not m:
        return None
    name = re.match(r"# (.+?) cost,", txt)
    return {"name": name.group(1) if name else "Chicane", "per_lap_s": float(m.group(1)),
            "low_s": float(m.group(2)), "high_s": float(m.group(3))}


NO_TRACK = {"id": "", "name": "", "known": False, "turn_numbers": "", "turns_note": ""}


def tools_sig():
    """Changes when an analysis script or a track definition changes, so cached days are redone."""
    h = hashlib.sha1()
    for root in (SCRIPTS, TRACKS):
        for dp, dn, fs in os.walk(root):
            dn[:] = sorted(d for d in dn if d != "__pycache__")
            for f in sorted(fs):
                if f.endswith((".py", ".json", ".csv")):
                    h.update(f.encode())
                    with open(os.path.join(dp, f), "rb") as fh:
                        h.update(fh.read())
    return h.hexdigest()[:12]


def day_bundle(folder, cfg, date_sessions=None):
    """Everything the viewer shows for one analysis folder, from the tools' own output files."""
    vj = os.path.join(folder, "viewer.json")
    if not os.path.exists(vj):
        return None
    v = json.load(open(vj, encoding="utf-8"))
    sessions = read_csv(os.path.join(folder, "laps", "sessions.csv"))
    laps = read_csv(os.path.join(folder, "laps", "laps_all.csv"))
    lapsec = read_csv(os.path.join(folder, "lap_sections.csv"))
    sec_cols = [c for c in (lapsec[0].keys() if lapsec else []) if re.match(r"s\d+_", c)]
    ideal = v["ideal_s"]
    order = date_sessions or [s["session"] for s in sessions]
    targets = cfg["ramp_targets_min"]
    out_sessions = []
    for s in sessions:
        name = s["session"]
        short = name[name.rfind("a_"):] if "a_" in name else name
        used = [r for r in lapsec if r["session"] == short and r["used"] == "True"]
        s_ideal = sum(min(float(r[c]) for r in used) for c in sec_cols) if used else None
        idx = order.index(name) if name in order else len(out_sessions)
        best = num(s["best_s"])
        out_sessions.append({
            "session": short, "number": idx + 1, "start": s["start"], "direction": s["direction"],
            "on_track_min": num(s["on_track_min"]),
            "target_min": targets[min(idx, len(targets) - 1)] if targets else None,
            "warmup": idx == 0,
            "laps_full": int(s["laps_full"] or 0), "laps_clean": int(s["laps_clean"] or 0),
            "laps_used": len(used), "best_s": best, "best": s["best"], "best_lap_no": s["best_lap_no"],
            "gap_to_ideal_s": round(best - ideal, 3) if best is not None else None,
            "session_ideal_s": round(s_ideal, 3) if s_ideal else None,
            "std_dev_s": num(s["std_dev_s"]), "spread_s": num(s["spread_s"]), "fade_s": num(s["fade_s"]),
            "avg_clean": s["avg_clean"],
        })
    lap_rows = []
    for r in laps:
        short = r["session"][r["session"].rfind("a_"):] if "a_" in r["session"] else r["session"]
        lap_rows.append({"session": short, "lap": int(r["lap"]), "type": r["type"], "direction": r["direction"],
                         "lap_s": num(r["lap_s"]), "clean": r["clean"] == "True"})
    placed = {(r["session"], int(r["lap"])): r for r in lapsec}
    for r in lap_rows:      # what the ideal-lap step worked out for this lap along the track
        p = placed.get((r["session"], r["lap"]))
        r["used"] = bool(p) and p["used"] == "True"
        r["why_out"] = p["why_out"] if p else ""
        r["sec_s"] = [num(p[c]) for c in sec_cols] if p else None
    tire = [{"turn": r["turn"], "session": r["session"], "laps": int(r["laps"]),
             "min_kph": num(r["min_kph"]), "peak_lat_g": num(r["peak_lat_g"])}
            for r in read_csv(os.path.join(folder, "tire_check.csv"))]
    phases = read_csv(os.path.join(folder, "corner_phases.csv"))
    for p in phases:
        for k, val in list(p.items()):
            if k not in ("corner", "side", "which", "lap"):
                p[k] = num(val)
    date = v["date"]
    try:
        iso = datetime.strptime(date, "%m/%d/%Y").strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        iso = date or ""
    return {
        "id": os.path.basename(folder), "date": iso, "vehicle": v["vehicle"], "direction": v["direction"],
        "track": v.get("track") or NO_TRACK, "best_lap": v["best_lap"], "best": fmt_lap(v["best_lap"]["time_s"]), "ideal_s": ideal, "ideal": fmt_lap(ideal),
        "gap_s": round(v["best_lap"]["time_s"] - ideal, 3), "ideal_whole_corners_s": v["ideal_whole_corners_s"],
        "laps_used": v["laps_used"], "lap_length_m": v["lap_length_m"], "geometry_from": v["geometry_from"],
        "path": v["path"], "sections": v["sections"], "corners": v["corners"], "apexes": v["apexes"],
        "trace": v["trace"], "delta": v.get("delta"), "stint": v.get("stint"), "grip": v.get("grip"), "sessions": out_sessions, "laps": lap_rows, "tire": tire,
        "tire_flags": parse_tire_md(os.path.join(folder, "tire_check.md")),
        "chicane": parse_chicane_md(os.path.join(folder, "chicane.md")), "corner_phases": phases,
        "ramp": {"label": cfg["ramp_label"], "targets_min": targets},
        "speed_unit": cfg["speed_unit"],
    }


# ---------------------------------------------------------------- folder watching and processing

def drop_compressed_copies(paths):
    """Race Studio keeps a compressed .xrz next to a session's .xrk. They are the same session, so
    where both are in one folder only the .xrk is used; a .xrz on its own is still read."""
    have = {os.path.splitext(p)[0].lower() for p in paths if p.lower().endswith(".xrk")}
    return [p for p in paths if not (p.lower().endswith(".xrz") and os.path.splitext(p)[0].lower() in have)]


class Processor:
    def __init__(self, cfg):
        self.cfg = cfg
        self.analysis = os.path.join(cfg["data_folder"], "analysis")
        os.makedirs(self.analysis, exist_ok=True)
        self.manifest_path = os.path.join(cfg["data_folder"], "manifest.json")
        try:
            self.manifest = json.load(open(self.manifest_path, encoding="utf-8"))
        except (OSError, ValueError):
            self.manifest = {}
        self.manifest.setdefault("files", {})   # path -> {sig, date, start, vehicle}
        self.manifest.setdefault("dates", {})   # date -> {sig, error}
        self.tools = tools_sig()
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.status = {"state": "idle", "message": "", "version": 0, "errors": {}, "last_scan": None}

    def save(self):
        tmp = self.manifest_path + ".tmp"
        json.dump(self.manifest, open(tmp, "w", encoding="utf-8"), indent=1)
        os.replace(tmp, self.manifest_path)

    def set(self, **kw):
        with self.lock:
            self.status.update(kw)

    def file_meta(self, path):
        st = os.stat(path)
        sig = f"{st.st_size}:{int(st.st_mtime)}"
        m = self.manifest["files"].get(path)
        if m and m["sig"] == sig:
            return m
        if time.time() - st.st_mtime < 5:
            return None     # still being copied
        import contextlib
        import io
        sys.path.insert(0, SCRIPTS)
        from libxrk import aim_xrk
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                md = aim_xrk(path).metadata
            d = datetime.strptime(f"{md.get('Log Date')} {md.get('Log Time')}", "%m/%d/%Y %H:%M:%S")
            m = {"sig": sig, "date": d.strftime("%Y-%m-%d"), "start": d.strftime("%H:%M:%S"),
                 "vehicle": md.get("Vehicle") or "", "error": ""}
        except Exception as e:     # unreadable or partial file
            m = {"sig": sig, "date": "", "start": "", "vehicle": "", "error": f"{type(e).__name__}: {e}"}
        self.manifest["files"][path] = m
        return m

    def scan(self):
        root = self.cfg["xrk_folder"]
        self.tools = tools_sig()
        found = []
        for dp, _, fs in os.walk(root):
            for f in fs:
                if f.lower().endswith(XRK_EXT):
                    found.append(os.path.join(dp, f))
        found = drop_compressed_copies(found)
        for p in list(self.manifest["files"]):
            if p not in found:
                del self.manifest["files"][p]
        by_date = {}
        bad = {}
        for i, p in enumerate(sorted(found)):
            if p not in self.manifest["files"] or self.manifest["files"][p]["sig"] != self._sig(p):
                self.set(state="working", message=f"Reading {os.path.basename(p)} ({i + 1} of {len(found)})")
            m = self.file_meta(p)
            if m is None:
                continue
            if m["error"]:
                bad[os.path.basename(p)] = m["error"]
                continue
            by_date.setdefault(m["date"], []).append(p)
        self.save()
        return by_date, bad

    @staticmethod
    def _sig(p):
        st = os.stat(p)
        return f"{st.st_size}:{int(st.st_mtime)}"

    def date_sig(self, files):
        h = hashlib.sha1(self.tools.encode())
        for p in sorted(files):
            h.update(f"{os.path.basename(p)}|{self.manifest['files'][p]['sig']}\n".encode())
        return h.hexdigest()

    def process_date(self, date, files):
        for old in os.listdir(self.analysis):
            if old == f"analysis_{date}" or old.startswith(f"analysis_{date}_"):
                shutil.rmtree(os.path.join(self.analysis, old), ignore_errors=True)
        cmd = [sys.executable, "-I", os.path.join(SCRIPTS, "run_day.py"), *sorted(files), "--out", self.analysis]
        r = subprocess.run(cmd, capture_output=True, text=True)
        log = (r.stdout or "") + (r.stderr or "")
        open(os.path.join(self.cfg["data_folder"], f"log_{date}.txt"), "w", encoding="utf-8").write(log)
        if r.returncode:
            tail = [l for l in log.strip().splitlines() if l.strip()][-3:]
            return "Analysis failed: " + " | ".join(tail)
        return ""

    def folders_for(self, date):
        return sorted(os.path.join(self.analysis, d) for d in os.listdir(self.analysis)
                      if (d == f"analysis_{date}" or d.startswith(f"analysis_{date}_"))
                      and os.path.exists(os.path.join(self.analysis, d, "viewer.json")))

    def date_session_order(self, date):
        """All sessions of a date in start order, across bikes and directions, for the ramp."""
        rows = []
        for f in self.folders_for(date):
            rows += read_csv(os.path.join(f, "laps", "sessions.csv"))
        return [r["session"] for r in sorted(rows, key=lambda r: r["start"] or "")]

    def bundles(self, date):
        order = self.date_session_order(date)
        return [b for b in (day_bundle(f, self.cfg, order) for f in self.folders_for(date)) if b]

    def write_summaries(self, date):
        for b in self.bundles(date):
            write_summary(b, self.cfg["summary_folder"])

    def run_once(self):
        by_date, bad = self.scan()
        changed = False
        for date in sorted(by_date, reverse=True):    # newest day first
            files = by_date[date]
            sig = self.date_sig(files)
            prev = self.manifest["dates"].get(date, {})
            if prev.get("sig") == sig and (prev.get("error") or self.folders_for(date)):
                continue
            self.set(state="working", message=f"Analysing {date} ({len(files)} files)")
            try:
                err = self.process_date(date, files)
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
            if not err:
                if not self.folders_for(date):
                    err = "No full laps found in these files"
                else:
                    try:
                        self.write_summaries(date)
                    except Exception as e:
                        err = f"Summary not written: {e}"
            self.manifest["dates"][date] = {"sig": sig, "error": err, "files": [os.path.basename(f) for f in files],
                                            "at": datetime.now().isoformat(timespec="seconds")}
            self.save()
            changed = True
            with self.lock:
                self.status["version"] += 1
        for date in list(self.manifest["dates"]):
            if date not in by_date:      # files removed from the folder: keep its analysis, forget the signature
                self.manifest["dates"].pop(date)
                changed = True
        if changed:
            self.save()
        errors = {d: v["error"] for d, v in self.manifest["dates"].items() if v.get("error")}
        errors.update({f: e for f, e in bad.items()})
        self.set(state="idle", message="", errors=errors, last_scan=datetime.now().strftime("%H:%M:%S"))

    def loop(self):
        while True:
            try:
                self.run_once()
            except Exception:
                self.set(state="idle", message="", errors={"scan": traceback.format_exc(limit=2)})
            self.wake.wait(self.cfg["scan_seconds"])
            self.wake.clear()

    def day_list(self):
        out = []
        for d in sorted(os.listdir(self.analysis), reverse=True):
            vj = os.path.join(self.analysis, d, "viewer.json")
            if not os.path.exists(vj):
                continue
            v = json.load(open(vj, encoding="utf-8"))
            try:
                iso = datetime.strptime(v["date"], "%m/%d/%Y").strftime("%Y-%m-%d")
            except (TypeError, ValueError):
                iso = v["date"] or ""
            out.append({"id": d, "date": iso, "vehicle": v["vehicle"], "direction": v["direction"],
                        "track": (v.get("track") or NO_TRACK)["name"], "best": fmt_lap(v["best_lap"]["time_s"]), "ideal": fmt_lap(v["ideal_s"])})
        out.sort(key=lambda r: (r["date"], r["id"]), reverse=True)
        return out

    def day(self, day_id):
        if not re.fullmatch(r"analysis_[\w.-]+", day_id or ""):
            return None
        folder = os.path.join(self.analysis, day_id)
        m = re.match(r"analysis_(\d{4}-\d{2}-\d{2})", day_id)
        order = self.date_session_order(m.group(1)) if m else None
        return day_bundle(folder, self.cfg, order)


def write_summary(b, root):
    """Small files for Drive: day.json (everything the viewer shows, ~130 KB), sessions.csv,
    laps.csv and one <session>.json per session."""
    out = os.path.join(root, b["id"].replace("analysis_", ""))
    os.makedirs(out, exist_ok=True)
    json.dump(b, open(os.path.join(out, "day.json"), "w", encoding="utf-8"), separators=(",", ":"))
    keys = ["number", "session", "start", "direction", "on_track_min", "target_min", "laps_full", "laps_clean",
            "best", "best_s", "gap_to_ideal_s", "session_ideal_s", "std_dev_s", "spread_s", "fade_s"]
    with open(os.path.join(out, "sessions.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(b["sessions"])
    with open(os.path.join(out, "laps.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["session", "lap", "type", "direction", "lap_s", "clean", "used", "why_out"],
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(b["laps"])
    for s in b["sessions"]:
        json.dump({"date": b["date"], "vehicle": b["vehicle"], "direction": b["direction"],
                   "day_ideal_s": b["ideal_s"], "session": s,
                   "laps": [r for r in b["laps"] if r["session"] == s["session"]],
                   "tire": [r for r in b["tire"] if r["session"] == s["session"]]},
                  open(os.path.join(out, f"{s['session']}.json"), "w", encoding="utf-8"), indent=1)


# ---------------------------------------------------------------- web server

class Handler(http.server.BaseHTTPRequestHandler):
    proc = None
    cfg = None

    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        p = u.path
        if p == "/api/status":
            with self.proc.lock:
                st = dict(self.proc.status)
            st["xrk_folder"] = self.cfg["xrk_folder"]
            st["summary_folder"] = self.cfg["summary_folder"]
            return self.send(200, st)
        if p == "/api/days":
            return self.send(200, self.proc.day_list())
        if p.startswith("/api/day/"):
            b = self.proc.day(urllib.parse.unquote(p[len("/api/day/"):]))
            return self.send(200, b) if b else self.send(404, {"error": "no such day"})
        name = "index.html" if p in ("/", "") else p.lstrip("/")
        f = os.path.normpath(os.path.join(STATIC, name))
        if not f.startswith(STATIC + os.sep) or not os.path.isfile(f):
            return self.send(404, b"not found", "text/plain")
        ctype = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}.get(os.path.splitext(f)[1], "application/octet-stream")
        return self.send(200, open(f, "rb").read(), ctype)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/api/rescan":
            self.proc.wake.set()
            return self.send(200, {"ok": True})
        if u.path == "/api/upload":
            q = urllib.parse.parse_qs(u.query)
            name = os.path.basename((q.get("name") or [""])[0].replace("\\", "/"))
            n = int(self.headers.get("Content-Length") or 0)
            if not name.lower().endswith(XRK_EXT) or not 0 < n < 200_000_000:
                return self.send(400, {"error": "only .xrk or .xrz files"})
            dest = os.path.join(self.cfg["xrk_folder"], "dropped")
            os.makedirs(dest, exist_ok=True)
            tmp = os.path.join(dest, name + ".part")
            with open(tmp, "wb") as f:
                left = n
                while left:
                    chunk = self.rfile.read(min(1 << 20, left))
                    if not chunk:
                        break
                    f.write(chunk)
                    left -= len(chunk)
            os.replace(tmp, os.path.join(dest, name))
            old = time.time() - 10      # let the scanner take it straight away
            os.utime(os.path.join(dest, name), (old, old))
            self.proc.wake.set()
            return self.send(200, {"ok": True, "saved": name})
        return self.send(404, {"error": "not found"})


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    ap = argparse.ArgumentParser(description="Pit lap viewer")
    ap.add_argument("--folder")
    ap.add_argument("--port", type=int)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    cfg = load_config()
    if a.folder:
        cfg["xrk_folder"] = os.path.abspath(a.folder)
    if a.port:
        cfg["port"] = a.port
    proc = Processor(cfg)
    print(f"Watching   {cfg['xrk_folder']}")
    print(f"Summaries  {cfg['summary_folder']}")
    if a.once:
        proc.run_once()
        for d in proc.day_list():
            print(f"  {d['date']} {d['track']} {d['vehicle']} {d['direction']}: best {d['best']}, ideal {d['ideal']}")
        for k, e in proc.status["errors"].items():
            print(f"  problem with {k}: {e}")
        return
    Handler.proc, Handler.cfg = proc, cfg
    threading.Thread(target=proc.loop, daemon=True).start()
    try:
        srv = Server(("127.0.0.1", cfg["port"]), Handler)
    except OSError:
        url = f"http://127.0.0.1:{cfg['port']}/"
        print(f"Port {cfg['port']} is busy; the viewer may already be running at {url}")
        if cfg["open_browser"] and not a.no_browser:
            webbrowser.open(url)
        return
    url = f"http://127.0.0.1:{cfg['port']}/"
    print(f"Lap viewer at {url}  (close this window to stop it)")
    if cfg["open_browser"] and not a.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
