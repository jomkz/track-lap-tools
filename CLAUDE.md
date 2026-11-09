# CLAUDE.md

Context for Claude Code sessions working in this repository.

## What this is

Lap analysis for AiM Solo DL logs (`.xrk`) at any track, plus an offline pit lap viewer that shows
each day full screen on a laptop or TV. Licensed AGPL-3.0 (see `LICENSE`).

- `scripts/` analysis tools (Python). `run_day.py` is the one command that runs the rest.
- `tracks/<id>/` one folder per known track: `track.json`, `turns.csv` (official turn numbers) and
  other reference files. `tracks/harris-hill/` (Harris Hill Road, San Marcos, TX) is the home track.
- `viewer/` local web app: `lapviewer.py` (standard library only) serves `static/` (plain HTML, CSS,
  SVG, no libraries) on `127.0.0.1` and runs `../scripts/run_day.py` for each new or changed day.
- `tests/` unit tests on made-up laps (they need no lap data).
- `README.md` and `viewer/README.md` hold the full usage and output reference. Keep them current
  when behaviour changes.

## Setup and running

- Python 3.10 to 3.14. Install once: `python3 -m pip install -r requirements.txt`
  (libxrk decodes `.xrk`; numpy, scipy, matplotlib, pyarrow).
- Tools: put a day's `.xrk` files in `raw/<date>/`, then `python3 -I scripts/run_day.py <date>`.
  Add `--vs <other date>` to compare two days (same track and direction only). Results go to
  `output/analysis_<date>/` (with a `_<bike>_<cw|ccw>` suffix when a day has more than one group).
- Viewer on Windows: double-click `viewer/start_windows.bat` (makes `.venv` on first run).
  On Linux: `bash viewer/start_linux.sh`. By hand: `python3 -I viewer/lapviewer.py`
  (`--folder`, `--port`, `--no-browser`, `--once`).
- Viewer settings live in `viewer/config.json` (made on first start, git-ignored): `xrk_folder`
  (where `.xrk` files are watched), `summary_folder` (small per-day results, can be a synced cloud
  folder), `data_folder` (analysis cache), `ramp_targets_min`, `ramp_label`, `speed_unit`
  (`kmh` or `mph`), `port` (default 8765), `scan_seconds`, `open_browser`.
- Checks after edits: `python3 -I -m py_compile scripts/*.py viewer/lapviewer.py` and
  `python3 -I -m unittest discover -s tests`.

## Data and analysis conventions

- Nothing in the code is specific to one track. What differs between tracks is data in
  `tracks/<id>/`; a track with no folder must still work, with corners numbered T1, T2... in riding
  order. Check a change on a day from a track that has no folder as well as on Harris Hill.
- A session's track comes from where its GPS points are (`scripts/tracks.py`). The AiM track name
  is only a cross-check and is often wrong (at Harris Hill it reads H2R / R2H).
- Track direction (clockwise or counterclockwise) also comes from the GPS path.
- Compare lap times only within the same track and direction.
- Turn numbers follow the track's own turn table. Harris Hill: the official map's 1 to 11, numbered
  in the clockwise direction (2 Carmen's Corner, 4 Santa Rita, 7 Mustang, 8 Dam Turn, 10 The
  Hairpin). Counterclockwise meets them in reverse, 11 down to 1, with the same numbers. Turn 3 is
  a gentle kink that doesn't show as a corner in the data.
- A track can define a temporary cone chicane (`chicanes` in `track.json`). Harris Hill has one at
  turn 6 on clockwise days; `xrk_chicane.py` estimates its cost per lap against
  `tracks/harris-hill/t6_natural_radius.csv` (built from counterclockwise laps).
- Laps: AiM's own out-lap and in-lap (as the logger marks them) never count. Keep the last full
  lap of a session; don't drop it as an in-lap.
- A lap the GPS did not fully record (samples missing for more than a second, or section times
  that don't add up to the lap time) keeps its lap time but is left out of everything worked out
  along the track, with the reason shown. Never let such a lap set a best section.
- Clean lap = no slower than 107% of the session's median full lap. The ideal lap leaves out laps
  slower than 103% of the day's best.
- The first session of a day is a warm-up; judge the day from session two on.
- Ideal lap = the sum of the best time in each section across the day's laps.
- Throttle and braking are inferred from GPS speed and acceleration (and IMU g). There is no
  throttle, brake or RPM channel in these logs.
- Tire-wear signal: a turn whose mid-corner speed and grip fall in every session.
- Sessions can sit a few metres apart in GPS; align before comparing sessions or days.

## Rules for changes

- Work on a feature branch and open a pull request against `main`. Never commit or push to `main`
  directly. The owner merges every pull request.
- Never commit lap data: no `.xrk`/`.xrz`/`.drk`, nothing from `raw/` or `output/`, no result
  CSV, JSON or PNG. Lap data lives outside this repo. Screenshots of real laps only after checking
  they hold no personal data. The `.gitignore` enforces most of this; keep it. Track definitions
  in `tracks/` are reference data, not lap data, and are committed.
- Never commit personal data. `.xrk` filenames contain the rider's full name, so don't copy
  filenames into code, docs or tests. No names, emails, cloud folder or file ids, or absolute
  paths. Grep the staged diff for these before every commit.
- Keep dependencies under permissive licenses (MIT, BSD, Apache and similar). The sole owner may
  relicense this code later, so don't add outside code without a contributor agreement.
