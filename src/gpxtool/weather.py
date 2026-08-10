"""Borrow ambient temperature from the Open-Meteo archive, since the watch has none.

The archive is hourly on a coarse grid, so a value is an approximation of the
air the rider actually rode through, not a measurement.
"""

import json
import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import timezone
from pathlib import Path

from .gpxio import (
    build_gpx,
    parse_time,
    point_latlon,
    point_time,
    read_root,
    segments_of,
    set_extension_value,
    write_root,
)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FIELD = "atemp"
GRID_DIGITS = 2


@dataclass
class WeatherReport:
    latitude: float
    longitude: float
    days: list[str]
    points: int = 0
    tagged: int = 0
    from_cache: bool = False
    samples: list[tuple[float, float]] = field(default_factory=list)


def cache_dir() -> Path:
    """Honour GPXTOOL_CACHE, else the usual per-user cache location."""
    override = os.environ.get("GPXTOOL_CACHE")
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / ".cache"
    return base / "gpx-toolkit" / "weather"


def _cache_path(latitude: float, longitude: float, first: str, last: str) -> Path:
    key = f"{latitude:.{GRID_DIGITS}f}_{longitude:.{GRID_DIGITS}f}_{first}_{last}.json"
    return cache_dir() / key


def fetch_hourly(latitude: float, longitude: float, first: str, last: str,
                 timeout: float = 30.0) -> tuple[dict, bool]:
    """Hourly temperature for the ride's days, cached on disk between runs."""
    path = _cache_path(latitude, longitude, first, last)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8")), True

    query = urllib.parse.urlencode({
        "latitude": f"{latitude:.{GRID_DIGITS}f}",
        "longitude": f"{longitude:.{GRID_DIGITS}f}",
        "start_date": first,
        "end_date": last,
        "hourly": "temperature_2m",
        "timezone": "UTC",
    })
    request = urllib.request.Request(f"{ARCHIVE_URL}?{query}", headers={"User-Agent": "gpx-toolkit"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except OSError as error:
        raise SystemExit(f"Open-Meteo archive unreachable: {error}")

    if "hourly" not in payload:
        raise SystemExit(f"Open-Meteo returned no hourly data: {payload.get('reason', payload)}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload, False


def _series(payload: dict) -> list[tuple[float, float]]:
    """Open-Meteo hours as (epoch seconds, celsius); it stamps them as 2026-07-30T14:00."""
    hourly = payload["hourly"]
    samples = []
    for stamp, value in zip(hourly["time"], hourly["temperature_2m"]):
        if value is None:
            continue
        text = stamp.rstrip("Z")
        if len(text) == 16:
            text += ":00"
        samples.append((parse_time(text).timestamp(), float(value)))
    return samples


def _at(samples: list[tuple[float, float]], when: float) -> float | None:
    """Straight line between the two surrounding hours."""
    if not samples or when < samples[0][0] or when > samples[-1][0]:
        return None
    low, high = 0, len(samples) - 1
    while low < high - 1:
        middle = (low + high) // 2
        if samples[middle][0] <= when:
            low = middle
        else:
            high = middle
    t0, v0 = samples[low]
    t1, v1 = samples[high]
    if t1 == t0:
        return v0
    return v0 + (v1 - v0) * (when - t0) / (t1 - t0)


def add_temperature(path: str, output: str) -> WeatherReport:
    root = read_root(path)
    segments = segments_of(root)
    points: list[ET.Element] = [point for segment in segments for point in segment]
    if not points:
        raise SystemExit(f"{path}: no trackpoints")

    latitude, longitude = point_latlon(points[0])
    days = sorted({point_time(point).astimezone(timezone.utc).strftime("%Y-%m-%d")
                   for point in (points[0], points[-1])})
    payload, cached = fetch_hourly(latitude, longitude, days[0], days[-1])
    samples = _series(payload)

    report = WeatherReport(latitude, longitude, days, points=len(points), from_cache=cached,
                           samples=samples)
    for point in points:
        value = _at(samples, point_time(point).timestamp())
        if value is None:
            continue
        set_extension_value(point, FIELD, f"{value:.1f}")
        report.tagged += 1

    write_root(build_gpx(root, segments, f"gpx-toolkit temp: {path}"), output)
    return report
