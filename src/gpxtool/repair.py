"""Repairs for noisy consumer-GPS tracks.

Phone recorders drop fixes, emit several fixes inside one second, and report a
lagging <speed> tag that sticks at a stale value after a dropout. Each function
here fixes one of those symptoms without changing how long the segment lasts.
"""

import xml.etree.ElementTree as ET
from datetime import timedelta

from .config import RepairConfig
from .geo import haversine
from .gpxio import (
    is_synthetic,
    make_point,
    point_float,
    point_latlon,
    point_time,
    set_point_float,
    set_point_time,
)

Points = list[ET.Element]


def _elapsed(points: Points) -> list[float]:
    start = point_time(points[0])
    return [(point_time(p) - start).total_seconds() for p in points]


def dedupe_timestamps(points: Points) -> int:
    """Spread fixes sharing one second across that second, keeping duration."""
    times = [point_time(p) for p in points]
    index = 0
    moved = 0
    while index < len(times):
        last = index
        while last + 1 < len(times) and times[last + 1] == times[index]:
            last += 1
        count = last - index + 1
        if count > 1:
            for offset in range(1, count):
                times[index + offset] = times[index] + timedelta(seconds=offset / count)
                moved += 1
        index = last + 1
    for point, value in zip(points, times):
        set_point_time(point, value)
    return moved


def fill_gaps(points: Points, config: RepairConfig) -> tuple[Points, int]:
    """Insert ~1 Hz interpolated points inside short dropouts."""
    filled = [points[0]]
    added = 0
    for previous, current in zip(points, points[1:]):
        start, end = point_time(previous), point_time(current)
        gap = (end - start).total_seconds()
        if 1.5 < gap <= config.fill_max_gap_s:
            lat0, lon0 = point_latlon(previous)
            lat1, lon1 = point_latlon(current)
            ele0, ele1 = point_float(previous, "ele"), point_float(current, "ele")
            steps = int(round(gap))
            for step in range(1, steps):
                ratio = step / steps
                filled.append(make_point(
                    lat0 + (lat1 - lat0) * ratio,
                    lon0 + (lon1 - lon0) * ratio,
                    ele0 + (ele1 - ele0) * ratio,
                    start + timedelta(seconds=gap * ratio),
                ))
                added += 1
        filled.append(current)
    return filled, added


def moving_average(values: list[float], half_width: int) -> list[float]:
    if half_width <= 0:
        return values
    smoothed = []
    for index in range(len(values)):
        low = max(0, index - half_width)
        high = min(len(values), index + half_width + 1)
        window = values[low:high]
        smoothed.append(sum(window) / len(window))
    return smoothed


def derive_speed(points: Points, window_s: float) -> list[float]:
    """Speed over a centred window, so a held position no longer reads as a stop.

    A receiver that repeats the previous coordinate makes adjacent-pair
    differencing alternate between 0 and a spike. The window is shrunk
    symmetrically near the ends of the track rather than left undefined.
    """
    seconds = _elapsed(points)
    coords = [point_latlon(p) for p in points]
    last = len(points) - 1
    half = window_s / 2
    speeds = []

    for index in range(len(points)):
        low, high = index, index
        while low > 0 and seconds[index] - seconds[low - 1] <= half:
            low -= 1
        while high < last and seconds[high + 1] - seconds[index] <= half:
            high += 1

        reach = min(index - low, high - index)
        low, high = index - reach, index + reach
        if low == high:
            low, high = (index, index + 1) if index < last else (index - 1, index)
        if low < 0 or high > last:
            speeds.append(0.0)
            continue

        span = seconds[high] - seconds[low]
        distance = haversine(*coords[low], *coords[high])
        speeds.append(distance / span if span > 0 else 0.0)

    return speeds


def recompute_speed(points: Points, config: RepairConfig) -> None:
    """Derive speed from travelled distance instead of trusting the <speed> tag."""
    speeds = moving_average(derive_speed(points, config.speed_window_s), config.speed_smooth)
    elevations = moving_average([point_float(p, "ele") for p in points], config.elevation_smooth)

    for point, speed, elevation in zip(points, speeds, elevations):
        set_point_float(point, "speed", speed)
        set_point_float(point, "ele", elevation, digits=2)


def _synthetic_runs(points: Points) -> list[tuple[int, int]]:
    runs: list[list[int]] = []
    for index, point in enumerate(points):
        if not is_synthetic(point):
            continue
        if runs and runs[-1][1] == index - 1:
            runs[-1][1] = index
        else:
            runs.append([index, index])
    return [(low, high) for low, high in runs]


def ramp_over_dropouts(points: Points, config: RepairConfig) -> list[tuple[str, str, float]]:
    """Replace a stuck-then-snap dropout with a straight ramp to the first good fix."""
    seconds = _elapsed(points)
    speeds = [point_float(p, "speed") for p in points]
    repaired = []

    for low, high in _synthetic_runs(points):
        anchor = max(0, low - 1)
        if speeds[anchor] >= config.reliable_speed:
            continue

        target = None
        for index in range(high + 1, len(points)):
            if seconds[index] - seconds[anchor] > config.max_ramp_s:
                break
            if speeds[index] >= config.reliable_speed:
                target = index
                break
        if target is None:
            continue

        stuck = [i for i in range(high + 1, target) if speeds[i] < config.frozen_speed]
        if not stuck or seconds[stuck[-1]] - seconds[high] < config.frozen_min_s:
            continue

        start_speed, end_speed = speeds[anchor], speeds[target]
        span = seconds[target] - seconds[anchor]
        for index in range(anchor + 1, target):
            ratio = (seconds[index] - seconds[anchor]) / span
            speeds[index] = start_speed + (end_speed - start_speed) * ratio
        repaired.append((
            point_time(points[anchor]).strftime("%H:%M:%S"),
            point_time(points[target]).strftime("%H:%M:%S"),
            span,
        ))

    for point, speed in zip(points, speeds):
        set_point_float(point, "speed", speed)
    return repaired


def clamp_acceleration(points: Points, config: RepairConfig) -> None:
    """Bound speed changes to what a rider can physically do."""
    seconds = _elapsed(points)
    speeds = [point_float(p, "speed") for p in points]

    for index in range(1, len(speeds)):
        step = seconds[index] - seconds[index - 1]
        if step > 0:
            speeds[index] = min(speeds[index], speeds[index - 1] + config.accel_max * step)
    for index in range(len(speeds) - 2, -1, -1):
        step = seconds[index + 1] - seconds[index]
        if step > 0:
            speeds[index] = min(speeds[index], speeds[index + 1] + config.decel_max * step)

    for point, speed in zip(points, speeds):
        set_point_float(point, "speed", speed)
