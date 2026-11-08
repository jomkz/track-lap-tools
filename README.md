# Track lap tools (Harris Hill, AiM Solo DL)

Lap analysis for AiM Solo DL logs (`.xrk`) at Harris Hill Road, plus a pit lap viewer
(`viewer/`) that shows it full screen on a laptop or TV. Lap data is not kept in this repository:
`raw/` and `output/` are local folders and are git-ignored.

For the viewer, see [viewer/README.md](viewer/README.md).

## One command

1. Put the day's AiM `.xrk` files in `raw/<date>/`.
2. Run, from this folder:

```
python3 -I scripts/run_day.py 2026-11-07
```

Add `--vs 2026-11-06` to also compare with another day (same direction only), for example the other bike.
One-time setup: `python3 -m pip install -r requirements.txt`.

A day can mix bikes and directions. Each bike and direction gets its own folder,
`output/analysis_<date>/` (or `analysis_<date>_<bike>_<cw|ccw>/` when a day has more than one).

## What each output is

| File | What it holds |
|---|---|
| `laps/sessions.csv`, `laps/laps_all.csv` | Lap times per session, direction from GPS, clean laps, consistency, fade (ramp check numbers) |
| `summary.md` | Best lap, ideal lap (best piece of every corner), corners ranked by time lost |
| `track_map.png` | Track map coloured by time lost, top 3 corners labelled |
| `speed_trace.png` | Speed around the lap, best lap against the ideal |
| `sections.csv`, `lap_sections.csv` | Every piece's time on every lap |
| `corner_phases*.csv` | Per corner: brake point, peak braking g, slowest point, throttle pickup, coasting |
| `tire_check.md` / `.csv` | Tire-wear signal: turns whose mid-corner speed and grip fell every session |
| `chicane.md` / `.png` / `_laps.csv` | Clockwise only: estimated cost of the turn 6 cone chicane per lap |
| `vs_<bike>_<date>/compare.md` | With `--vs`: piece-by-piece comparison, braking/entry vs drive/straights |

## Rules the tools follow

- Lap times are compared only within one direction. Direction comes from the GPS path, not the AiM track name.
- Laps more than 3% off the day's best are left out. AiM's own out-lap and in-lap are never counted.
- The first session is a warm-up; read the day from session two on.
- Turn numbers are the official Harris Hill Road map's, 1 to 11 in the clockwise direction
  (2 Carmen's Corner, 4 Santa Rita, 7 Mustang, 8 Dam Turn, 10 The Hairpin; 3 is a kink that doesn't show as a corner).
  Counterclockwise meets them in reverse order, 11 down to 1, with the same numbers.
- Throttle and braking are inferred from GPS speed and g. There is no throttle, brake or RPM channel.

## Scripts

| Script | Job |
|---|---|
| `scripts/run_day.py` | The one command: runs everything below for a day |
| `scripts/xrk_laps.py` | Decode `.xrk`, lap table, direction, consistency and fade |
| `scripts/xrk_ideal_lap.py` | Ideal lap, time lost by corner, corner phases, map and speed trace |
| `scripts/xrk_tire.py` | Tire-wear signal (matched both corners the rider felt on a worn rear) |
| `scripts/xrk_chicane.py` | Turn 6 chicane estimate; `--make-radius` rebuilds `t6_natural_radius.csv` from counterclockwise laps |
| `scripts/xrk_compare.py` | Two sets of laps compared piece by piece |
| `scripts/track_common.py` | Shared loading, GPS alignment and turn lookup |
| `scripts/harris_hill_turns.csv` | Official turn positions (lat/lon) |
| `scripts/t6_natural_radius.csv` | Turn 6's flat-out line radius, from the Oct 26 counterclockwise laps |

`viewer.json` (written by `xrk_ideal_lap.py`) holds the track path, sections and speed traces for the viewer.
`harris_hill_turns.csv` and `t6_natural_radius.csv` are track reference data the scripts need.

## License

Copyright (C) 2026 jomkz

Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0). See [LICENSE](LICENSE).
Anyone who shares a changed copy, or runs one as a web service, must publish their source under the same license.
