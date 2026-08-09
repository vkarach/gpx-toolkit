"""Diagnostics for spotting what a recorder got wrong."""

from dataclasses import dataclass

from .geo import haversine
from .gpxio import point_float, point_latlon, point_time, read_segments


@dataclass
class Anomaly:
    kind: str
    at: str
    detail: str


def scan(path: str, accel_limit: float = 3.0) -> list[list[Anomaly]]:
    """Report duplicate timestamps, dropouts and impossible speed steps per segment."""
    _, segments = read_segments(path)
    findings = []

    for points in segments:
        issues: list[Anomaly] = []
        for previous, current in zip(points, points[1:]):
            start, end = point_time(previous), point_time(current)
            step = (end - start).total_seconds()
            stamp = end.strftime("%H:%M:%S")

            if step == 0:
                issues.append(Anomaly("duplicate", stamp, "two fixes share one timestamp"))
                continue
            if step > 1.5:
                issues.append(Anomaly("dropout", stamp, f"{step:.0f}s with no fix"))

            change = point_float(current, "speed") - point_float(previous, "speed")
            if step > 0 and abs(change / step) > accel_limit:
                issues.append(Anomaly(
                    "speed-step", stamp,
                    f"{change / step:+.1f} m/s2 ({point_float(previous, 'speed') * 3.6:.0f} -> "
                    f"{point_float(current, 'speed') * 3.6:.0f} km/h)",
                ))
        findings.append(issues)
    return findings


def tag_vs_distance(path: str, segment: int = 0, start: str = "", end: str = "") -> list[tuple[str, float, float, float]]:
    """Compare the recorded <speed> tag against speed implied by the coordinates."""
    _, segments = read_segments(path)
    points = segments[segment]
    rows = []
    for previous, current in zip(points, points[1:]):
        stamp = point_time(current).strftime("%H:%M:%S")
        if start and stamp < start:
            continue
        if end and stamp > end:
            break
        step = (point_time(current) - point_time(previous)).total_seconds()
        lat0, lon0 = point_latlon(previous)
        lat1, lon1 = point_latlon(current)
        distance = haversine(lat0, lon0, lat1, lon1)
        derived = distance / step * 3.6 if step > 0 else float("nan")
        rows.append((stamp, point_float(current, "speed") * 3.6, derived, distance))
    return rows
