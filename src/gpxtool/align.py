"""Locate the stretch of a long recording that matches a short one.

A watch logs the whole session while the camera only covers a few minutes of it,
and the two clocks rarely agree exactly. Instead of trusting timestamps, this
searches for the time offset where the donor's trajectory sits on top of the
main track, which both finds the right stretch and corrects the clock drift.
"""

import xml.etree.ElementTree as ET
from bisect import bisect_left
from dataclasses import dataclass
from statistics import median

from .geo import haversine
from .gpxio import point_latlon, point_time, wall_time

Points = list[ET.Element]


@dataclass
class Track:
    seconds: list[float]
    lats: list[float]
    lons: list[float]

    def __len__(self) -> int:
        return len(self.seconds)


@dataclass
class AlignReport:
    offset_s: float
    median_error_m: float
    p90_error_m: float
    coverage: float
    donor_window: tuple[str, str]
    trusted: bool


def donor_track(points: Points) -> Track:
    return Track(
        seconds=[point_time(p).timestamp() for p in points],
        lats=[float(p.get("lat")) for p in points],
        lons=[float(p.get("lon")) for p in points],
    )


def main_track(points: Points) -> Track:
    coords = [point_latlon(p) for p in points]
    return Track(
        seconds=[wall_time(p).timestamp() for p in points],
        lats=[lat for lat, _ in coords],
        lons=[lon for _, lon in coords],
    )


def _position_at(track: Track, when: float, max_gap_s: float = 15.0):
    """Interpolated donor position, or None outside the recording."""
    if when < track.seconds[0] or when > track.seconds[-1]:
        return None
    index = bisect_left(track.seconds, when)
    if index == 0:
        return track.lats[0], track.lons[0]
    low, high = index - 1, min(index, len(track) - 1)
    t0, t1 = track.seconds[low], track.seconds[high]
    if t1 - t0 > max_gap_s:
        return None
    if t1 == t0:
        return track.lats[low], track.lons[low]
    ratio = (when - t0) / (t1 - t0)
    return (track.lats[low] + (track.lats[high] - track.lats[low]) * ratio,
            track.lons[low] + (track.lons[high] - track.lons[low]) * ratio)


def _errors(main: Track, donor: Track, offset: float) -> list[float]:
    distances = []
    for second, lat, lon in zip(main.seconds, main.lats, main.lons):
        position = _position_at(donor, second + offset)
        if position is not None:
            distances.append(haversine(lat, lon, position[0], position[1]))
    return distances


def _score(main: Track, donor: Track, offset: float) -> tuple[float, float]:
    """Median position error and how much of the main track the donor covers."""
    distances = _errors(main, donor, offset)
    coverage = len(distances) / len(main)
    if coverage < 0.5:
        return float("inf"), coverage
    return median(distances), coverage


def find_offset(main_points: Points, donor_points: Points, max_shift_s: float = 900.0,
                coarse_step_s: float = 5.0, trust_error_m: float = 40.0) -> AlignReport:
    main = main_track(main_points)
    donor = donor_track(donor_points)

    best_offset, best_error, best_coverage = 0.0, float("inf"), 0.0
    step = coarse_step_s
    centre = 0.0
    span = max_shift_s

    while step >= 0.5:
        offset = centre - span
        while offset <= centre + span:
            error, coverage = _score(main, donor, offset)
            if error < best_error:
                best_offset, best_error, best_coverage = offset, error, coverage
            offset += step
        centre = best_offset
        span = step
        step /= 5

    distances = sorted(_errors(main, donor, best_offset))
    p90 = distances[int(0.9 * (len(distances) - 1))] if distances else float("inf")

    first = min(main.seconds) + best_offset
    last = max(main.seconds) + best_offset
    window = (
        _stamp(donor, first),
        _stamp(donor, last),
    )

    return AlignReport(
        offset_s=best_offset,
        median_error_m=best_error,
        p90_error_m=p90,
        coverage=best_coverage,
        donor_window=window,
        trusted=best_error <= trust_error_m and best_coverage >= 0.8,
    )


def _stamp(donor: Track, when: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(when, tz=timezone.utc).strftime("%H:%M:%S")
