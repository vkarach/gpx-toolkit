# gpx-toolkit

Repairs consumer-GPS tracks (phone dashcams, action cams) so they drive a clean
telemetry overlay, and merges several recordings into one continuous track.

No third-party dependencies. Python 3.10+.

## Why

Phone recorders produce tracks that look wrong on a speed gauge:

- several fixes stamped with the same second, so the gauge jumps instantly
- dropouts of a few seconds where the receiver writes nothing
- a `<speed>` tag that sticks at a stale value after a dropout, then snaps
- elevation noise that makes a slope readout flicker

Merging two recordings adds another problem: the pause between them becomes a
hole where the overlay shows 0.

## Usage

```
python -m gpxtool merge ride_1.gpx ride_2.gpx -o ride_merged.gpx
python -m gpxtool enrich ride_merged.gpx watch.gpx -o ride_final.gpx
python -m gpxtool scan ride_2.gpx
python -m gpxtool compare ride_2.gpx --start 15:08:45 --end 15:09:05
```

Run from the repo root with `PYTHONPATH=src`, or `pip install -e .` to get the
`gpxtool` command.

### merge

Each input segment is repaired, then segments are stitched end to end so the
merged track has no dead time. Source files are never modified.

Per segment, in order:

1. duplicate timestamps spread across their own second (duration unchanged)
2. dropouts up to `--fill-max-gap-s` filled with 1 Hz interpolated points
3. speed recomputed from travelled distance, ignoring the `<speed>` tag
4. speed and elevation smoothed over a small window
5. a stuck-then-snap dropout replaced with a straight ramp to the first good fix
6. speed changes clamped to plausible acceleration and braking

Synthetic points carry `interpolated="true"`.

### enrich

Positional order decides authority: the first file is the source of truth and
keeps its geometry, timing and speed; the second only lends sensor channels it
has and the first lacks.

```
python -m gpxtool enrich dashcam.gpx watch.gpx -o out.gpx
python -m gpxtool enrich dashcam.gpx watch.gpx -o out.gpx --fields hr,cad --shift-s -3
```

Every `TrackPointExtension` field in the donor is copied by default (`hr`,
`cad`, `atemp`, `power`, ...). Values are interpolated between the donor's
samples, so the two recorders do not need the same sample rate. Donor gaps
longer than `--max-gap-s` are left empty rather than invented.

#### Finding the right stretch

A watch usually logs the whole session while the camera covers a few minutes of
it, and the two clocks rarely agree exactly. Timestamps alone are therefore not
enough: a few seconds of drift silently hands every point the wrong reading.

So the donor is aligned by trajectory. The tool searches clock offsets within
`--max-shift-s` for the one where the donor's coordinates sit closest to the
main track, and reports what it found:

```
matched stretch 15:01:58 -> 15:12:59 at offset +2.0s
position error: median 5 m, p90 12 m, coverage 100%
```

A few metres of median error means the same ride; hundreds of metres, or low
coverage, means the files do not belong together and the run aborts rather than
writing plausible-looking nonsense. Override with `--shift-s` to force an offset
or `--no-require-alignment` to enrich anyway.

Merging rewrites timestamps to close the pause between recordings, so every
point also carries `srctime` with its original wall clock. `enrich` matches on
that, which means it works the same before or after a merge.

The command reports coverage, for example `hr: 343/343 points (100%)`. A low
percentage means the clocks or the time ranges do not line up.

### scan

Lists duplicates, dropouts and physically impossible speed steps. Useful before
and after a repair to see what changed.

### compare

Prints the recorded `<speed>` tag next to the speed implied by the coordinates.
Where the two disagree, the tag is usually the one lying.

## Tuning

Every field of `RepairConfig` is exposed as a `merge` flag, for example
`--speed-window-s 3.0` or `--accel-max 2.0`. Defaults are documented in
`src/gpxtool/config.py`.

## Layout

```
src/gpxtool/
  gpxio.py     parsing, serialisation, point accessors
  geo.py       haversine distance
  config.py    tunables
  repair.py    per-segment repairs
  pipeline.py  merge and stitch
  align.py     find where two recordings overlap by trajectory
  enrich.py    borrow sensor channels from a donor recording
  inspect.py   diagnostics
  cli.py       command line
```
