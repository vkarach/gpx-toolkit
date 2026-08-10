# gpx-toolkit

Repairs consumer-GPS tracks (watches, phone dashcams, action cams) so they drive
a clean telemetry overlay, merges several recordings into one continuous track,
and lines a video up with the track that belongs to it.

No third-party dependencies. Python 3.10+.

## Why

Watch and phone recorders produce tracks that look wrong on a speed gauge:

- several fixes stamped with the same second, so the gauge jumps instantly
- dropouts of a few seconds where the receiver writes nothing
- a `<speed>` tag that sticks at a stale value after a dropout, then snaps
- elevation noise that makes a slope readout flicker

Merging two recordings adds another problem: the pause between them becomes a
hole where the overlay shows 0.

## Usage

```
python -m gpxtool normalize watch_export.gpx -o ride.gpx
python -m gpxtool temp ride.gpx -o ride_with_temp.gpx
python -m gpxtool sync ride.gpx DJI_0042.MP4 --camera-tz +02:00
python -m gpxtool clip ride.gpx DJI_0042.MP4 -o ride_clip.gpx --camera-tz +02:00
python -m gpxtool merge ride_1.gpx ride_2.gpx -o ride_merged.gpx
python -m gpxtool enrich ride_merged.gpx watch.gpx -o ride_final.gpx
python -m gpxtool scan ride_2.gpx
python -m gpxtool compare ride_2.gpx --start 15:08:45 --end 15:09:05
```

Run from the repo root with `PYTHONPATH=src`, or `pip install -e .` to get the
`gpxtool` command.

Every command reads its inputs and writes a new file; it refuses to overwrite an
input. `normalize` and `scan` also accept directories and process every `.gpx`
inside, with `--out-dir` instead of `-o` for a batch.

### normalize

Prepares a Samsung Health export (Galaxy Watch) for an overlay tool, fixing what
the exporter gets wrong:

- `<metadate>` rewritten as `<metadata><time>`, which is what GPX 1.1 defines
- `<exerciseinfo>` kept, but moved under `<extensions>` where it validates
- points with no `gpxtpx:hr`: `--missing-hr fill` forward-fills the last reading
  (default), `drop` cuts a tail that has none, `keep` leaves them empty
- a pause longer than `--gap-split-s` (default 10 s) ends the `<trkseg>`, so
  nothing downstream interpolates across it
- `<speed>` derived per point, see below

The result is checked against the GPX 1.1 content model before anything is
written, and nothing is written if it would be invalid. `<speed>` is reported as
a warning rather than an error: it is GPX 1.0 vocabulary that overlay tools read
anyway, and dropping it would leave the gauge with nothing to show.

The run prints a summary per file: point count, duration, sampling rate, share
of held positions, points without heart rate, and every gap detected.

### Derived speed

Samsung stores no instantaneous speed, and adjacent-pair differencing produces a
sawtooth: a receiver at a traffic light or under a weak fix repeats the previous
coordinate, giving 0 m/s followed by a spike. In a reference 1 hour ride, 42% of
points repeat their predecessor.

Speed is therefore measured over a centred window,
`v[i] = distance(p[i - n/2], p[i + n/2]) / (t[i + n/2] - t[i - n/2])`, with
`--speed-window-s` (default 5, sane range 3-7). Near the ends of a track the
window shrinks symmetrically instead of leaving the speed undefined. Distance is
haversine; elevation is left out of the horizontal speed, and can be smoothed
first with `--elevation-smooth` when a gradient readout flickers.

### sync

The camera has no GPS, so the video is placed by clock offset. The GPX is never
trimmed - trimming only adds another place to be a few seconds out.

```
python -m gpxtool sync ride.gpx DJI_0042.MP4 --camera-tz +02:00
```

```
video start 2026-07-30 14:35:00 UTC
track start 2026-07-30 14:28:55 UTC, 3628s long
offset +365.0s (+0:06:05.0)
```

The video's start comes from the MP4 `mvhd` creation time. Cameras write that as
local time with no offset attached while the GPX is UTC, so the camera's
timezone is a required parameter - a fixed offset like `+02:00` or an IANA name
- and is never guessed from the host machine. An offset that is negative or
longer than the track warns loudly: that means clock drift, the wrong timezone,
or the wrong pair of files.

The spec calls that stamp the moment the movie was created, but plenty of phones
write it when the file is closed, which puts the video a whole duration away
from where it belongs. Both readings are therefore measured against the track -
how far it travelled, how much it climbed, how much of the window it covers -
and the better fit is chosen, with `--stamp start|end` to force one. The output
shows both, so a bad fit is visible rather than silent:

```
     mvhd as start 14:19:44 - 14:28:24 UTC: 777 m travelled, 5.4 km/h, 19 m climbed, 100% covered
  -> mvhd as end   14:11:03 - 14:19:44 UTC: 1285 m travelled, 8.9 km/h, 12 m climbed, 100% covered
```

### clip

When the overlay wants a track exactly as long as the video rather than an
offset into an hours-long ride:

```
python -m gpxtool clip ride.gpx DJI_0042.MP4 -o ride_clip.gpx --camera-tz UTC
```

The window is resolved exactly as `sync` resolves it, `--pad-s` keeps a margin
either side, and timestamps stay as recorded so a later `enrich` still matches.

### temp

The watch has no thermometer, so ambient temperature can only be borrowed:

```
python -m gpxtool temp ride.gpx -o ride_with_temp.gpx
```

Historical weather comes from the Open-Meteo archive API (free, no key) for the
first trackpoint's coordinates and the ride's date, written as `gpxtpx:atemp`
next to `gpxtpx:hr`. The archive is hourly on a coarse grid, so values are
interpolated between hours and are an approximation, not a measurement.
Responses are cached on disk under `%LOCALAPPDATA%\gpx-toolkit\weather` (or
`~/.cache/gpx-toolkit/weather`, or `$GPXTOOL_CACHE`) keyed by rounded
coordinates and date, so iterating does not refetch.

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
`--speed-window-s 3.0` or `--accel-max 2.0`, and `NormalizeConfig` as a
`normalize` flag. Defaults are documented in `src/gpxtool/config.py`.

## Layout

```
src/gpxtool/
  gpxio.py     parsing, serialisation, point accessors
  geo.py       haversine distance
  config.py    tunables
  repair.py    per-segment repairs and derived speed
  pipeline.py  merge and stitch
  normalize.py Samsung Health export fixes, segment splitting, per-file summary
  validate.py  GPX 1.1 content model check, no schema library needed
  align.py     find where two recordings overlap by trajectory
  enrich.py    borrow sensor channels from a donor recording
  video.py     MP4 creation time, duration, and the window that fits the track
  clip.py      cut a track down to a video's window
  weather.py   ambient temperature from the Open-Meteo archive
  inspect.py   diagnostics
  cli.py       command line
```
