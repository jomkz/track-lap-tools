# Track lap tools (AiM Solo DL)

Lap analysis for AiM Solo DL logs (`.xrk`) at any track, plus a pit lap viewer (`viewer/`) that
shows it full screen on a laptop or TV. Lap data is not kept in this repository: `raw/` and
`output/` are local folders and are git-ignored.

For the viewer, see [viewer/README.md](viewer/README.md).

## One command

1. Put the day's AiM `.xrk` files in `raw/<date>/`.
2. Run, from this folder:

```
python3 -I scripts/run_day.py 2026-11-07
```

Add `--vs 2026-11-06` to also compare with another day (same track and direction only), for example the other bike.
One-time setup: `python3 -m pip install -r requirements.txt`.

A day can mix bikes and directions. Each bike and direction gets its own folder,
`output/analysis_<date>/` (or `analysis_<date>_<bike>_<cw|ccw>/` when a day has more than one).

## What each output is

| File | What it holds |
|---|---|
| `laps/sessions.csv`, `laps/laps_all.csv` | Lap times per session, track, direction from GPS, clean laps, consistency, fade (ramp check numbers) |
| `summary.md` | Best lap, ideal lap (best piece of every corner), corners ranked by time lost, laps left out and why |
| `track_map.png` | Track map coloured by time lost, top 3 corners labelled |
| `speed_trace.png` | Speed around the lap, best lap against the ideal |
| `sections.csv`, `lap_sections.csv` | Every piece's time on every lap |
| `corners.csv` | The corners found on the day, with the position of each apex |
| `corner_phases*.csv` | Per corner: brake point, peak braking g, slowest point, throttle pickup, coasting |
| `tire_check.md` / `.csv` | Tire-wear signal: turns whose mid-corner speed and grip fell every session |
| `chicane.md` / `.png` / `_laps.csv` | Only where the track defines a cone chicane: its estimated cost per lap |
| `vs_<bike>_<date>/compare.md` | With `--vs`: piece-by-piece comparison, braking/entry vs drive/straights |

## Tracks

The tools work at any circuit. A session's track is found from where its GPS points are, never
from the track name set in the logger (which is often left on another setting).

- **A track with a folder in `tracks/`** gets its name and its official turn numbers, the same in
  both directions. `tracks/harris-hill/` (Harris Hill Road, San Marcos, Texas) is included: turns
  1 to 11 as on the official map, numbered in the clockwise direction (2 Carmen's Corner, 4 Santa
  Rita, 7 Mustang, 8 Dam Turn, 10 The Hairpin; 3 is a kink that doesn't show as a corner), so
  counterclockwise meets them in reverse, 11 down to 1.
- **Any other track** is named after the logger's track name, and its corners are numbered T1, T2...
  in riding order from the start/finish line. Those numbers may not match the official map, and
  they differ between directions.

To give a track its own folder, analyse one day there and run:

```
python3 -I scripts/track_add.py "Track name" output/analysis_<date> --place "Town, State"
```

It writes `tracks/<id>/track.json` (name, centre, radius) and `tracks/<id>/turns.csv` (one row per
corner found that day, in riding order). Edit `turns.csv` to match the official map: change the
numbers, add names, add a turn the data missed, delete a row that isn't a real turn. A position only
needs to be within about 100 m of the apex. Track folders hold no lap data and can be committed.

`track.json` can also list the direction each logger track name implies (`logger_track_names`, a
cross-check against the GPS direction) and temporary cone chicanes to cost out (`chicanes`). The
fields are described at the top of `scripts/tracks.py` and `scripts/xrk_chicane.py`.

## Rules the tools follow

- Lap times are compared only within one track and one direction. Both come from the GPS path, not the AiM track name.
- Laps more than 3% off the day's best are left out. AiM's own out-lap and in-lap are never counted.
- A lap the GPS did not fully record (the logger dropped samples for more than a second, or the
  lap's pieces don't add up to its lap time) keeps its lap time in the lap table but is left out of
  everything worked out along the track. `summary.md` lists each one with the reason.
- The first session is a warm-up; read the day from session two on.
- Throttle and braking are inferred from GPS speed and g. There is no throttle, brake or RPM channel.

## Scripts

| Script | Job |
|---|---|
| `scripts/run_day.py` | The one command: runs everything below for a day |
| `scripts/xrk_laps.py` | Decode `.xrk`, lap table, direction, consistency and fade |
| `scripts/xrk_ideal_lap.py` | Ideal lap, time lost by corner, corner phases, map and speed trace |
| `scripts/xrk_tire.py` | Tire-wear signal (at Harris Hill it matched both corners the rider felt on a worn rear) |
| `scripts/xrk_chicane.py` | Cone chicane estimate for tracks that define one; `--make-radius` rebuilds the natural-radius file from laps run the other way |
| `scripts/xrk_compare.py` | Two sets of laps compared piece by piece |
| `scripts/track_add.py` | Make a `tracks/<id>/` folder from an analysed day |
| `scripts/tracks.py` | Track definitions and matching a session to its track |
| `scripts/track_common.py` | Shared loading, GPS alignment and turn lookup |
| `scripts/lap_views.py` | Numbers for the viewer's extra views (time delta trace, stint trend, late against early), as functions on arrays |
| `scripts/lap_rules.py` | What a clean lap and fade mean, shared by the scripts |
| `tracks/<id>/` | One folder per known track: `track.json`, `turns.csv` and other reference files |

`viewer.json` (written by `xrk_ideal_lap.py`) holds the track path, sections and speed traces for the viewer,
and what its other views need, such as each lap's time delta to the best lap along the track
(worked out in `scripts/lap_views.py`).

## Tests

```
python3 -I -m unittest discover -s tests
```

The tests use made-up laps, so they need no lap data.

## License

Copyright (C) 2026 jomkz

Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0). See [LICENSE](LICENSE).
Anyone who shares a changed copy, or runs one as a web service, must publish their source under the same license.
