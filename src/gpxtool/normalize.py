"""Turn a Samsung Health export into a valid GPX 1.1 file an overlay tool can read.

Samsung writes <metadate> instead of <metadata>, keeps a non-standard
<exerciseinfo> block, drops heart rate from the tail of a ride, and leaves the
whole activity in one <trkseg> even when it was paused.
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from statistics import median

from .config import NormalizeConfig
from .gpxio import (
    build_gpx,
    extension_value,
    format_time,
    parse_time,
    point_float,
    point_latlon,
    point_time,
    q,
    read_root,
    segments_of,
    set_extension_value,
    set_point_float,
    write_root,
)
from .repair import derive_speed, moving_average
from .validate import ERROR, Issue, validate

Points = list[ET.Element]

HR = "hr"
MISSING_HR_MODES = ("fill", "drop", "keep")


@dataclass
class FileSummary:
    path: str
    points: int = 0
    duration_s: float = 0.0
    sample_rate_hz: float = 0.0
    duplicate_coordinates: int = 0
    missing_hr: int = 0
    gaps: list[tuple[str, float]] = field(default_factory=list)

    @property
    def duplicate_share(self) -> float:
        return self.duplicate_coordinates / self.points if self.points else 0.0


@dataclass
class NormalizeReport:
    summary: FileSummary
    metadata_fixed: bool = False
    exerciseinfo_moved: bool = False
    hr_filled: int = 0
    points_dropped: int = 0
    segments: int = 1
    issues: list[Issue] = field(default_factory=list)


def summarize(path: str, points: Points, gap_split_s: float) -> FileSummary:
    """Point count, duration, sampling rate, held positions, missing HR and pauses."""
    summary = FileSummary(path=path, points=len(points))
    if not points:
        return summary

    steps = []
    for previous, current in zip(points, points[1:]):
        step = (point_time(current) - point_time(previous)).total_seconds()
        steps.append(step)
        if point_latlon(previous) == point_latlon(current):
            summary.duplicate_coordinates += 1
        if step > gap_split_s:
            summary.gaps.append((point_time(previous).strftime("%H:%M:%S"), step))

    summary.duration_s = (point_time(points[-1]) - point_time(points[0])).total_seconds()
    typical = median(steps) if steps else 0.0
    summary.sample_rate_hz = 1 / typical if typical > 0 else 0.0
    summary.missing_hr = sum(1 for point in points if extension_value(point, HR) is None)
    return summary


def fix_metadata(root: ET.Element) -> bool:
    """Samsung's <metadate> is not a GPX element; rewrite it as <metadata><time>."""
    broken = root.find(q("metadate"))
    if broken is None:
        return False
    root.remove(broken)
    metadata = root.find(q("metadata"))
    if metadata is None:
        metadata = ET.Element(q("metadata"))
        root.insert(0, metadata)
    if metadata.find(q("time")) is None and broken.text:
        ET.SubElement(metadata, q("time")).text = format_time(parse_time(broken.text.strip()))
    return True


def move_exerciseinfo(root: ET.Element) -> bool:
    """Keep the ride summary, but under <extensions> where the schema allows it."""
    block = root.find(q("exerciseinfo"))
    if block is None:
        return False
    root.remove(block)
    extensions = root.find(q("extensions"))
    if extensions is None:
        extensions = ET.SubElement(root, q("extensions"))
    extensions.append(block)
    return True


def apply_missing_hr(points: Points, mode: str) -> tuple[Points, int, int]:
    """Forward-fill heart rate, drop the trailing points that lack it, or keep them."""
    if mode not in MISSING_HR_MODES:
        raise SystemExit(f"unknown --missing-hr {mode!r}, expected one of {', '.join(MISSING_HR_MODES)}")
    if mode == "keep":
        return points, 0, 0

    if mode == "drop":
        end = len(points)
        while end > 0 and extension_value(points[end - 1], HR) is None:
            end -= 1
        return points[:end], 0, len(points) - end

    filled = 0
    last = None
    for point in points:
        value = extension_value(point, HR)
        if value is not None:
            last = value
        elif last is not None:
            set_extension_value(point, HR, last)
            filled += 1
    return points, filled, 0


def split_on_gaps(points: Points, gap_split_s: float) -> list[Points]:
    """A pause longer than the threshold starts a new segment."""
    segments: list[Points] = [[]]
    for previous, current in zip([points[0]] + points, points):
        if segments[-1] and (point_time(current) - point_time(previous)).total_seconds() > gap_split_s:
            segments.append([])
        segments[-1].append(current)
    return segments


def _write_speed(points: Points, config: NormalizeConfig) -> None:
    if config.elevation_smooth:
        elevations = moving_average([point_float(p, "ele") for p in points], config.elevation_smooth)
        for point, elevation in zip(points, elevations):
            set_point_float(point, "ele", elevation, digits=3)
    for point, speed in zip(points, derive_speed(points, config.speed_window_s)):
        set_point_float(point, "speed", speed)


def normalize_file(path: str, output: str, config: NormalizeConfig) -> NormalizeReport:
    root = read_root(path)
    points = [point for segment in segments_of(root) for point in segment]
    if not points:
        raise SystemExit(f"{path}: no trackpoints")

    report = NormalizeReport(summary=summarize(path, points, config.gap_split_s))
    report.metadata_fixed = fix_metadata(root)
    report.exerciseinfo_moved = move_exerciseinfo(root)
    points, report.hr_filled, report.points_dropped = apply_missing_hr(points, config.missing_hr)
    if not points:
        raise SystemExit(f"{path}: every point lacks heart rate, nothing left after --missing-hr drop")

    segments = split_on_gaps(points, config.gap_split_s)
    report.segments = len(segments)
    if config.derive_speed:
        for segment in segments:
            _write_speed(segment, config)

    built = build_gpx(root, segments, f"gpx-toolkit normalize: {path}")
    report.issues = validate(built)
    broken = [issue for issue in report.issues if issue.level == ERROR]
    if broken:
        raise SystemExit(f"{path} would not produce valid GPX 1.1, nothing written:\n  " +
                         "\n  ".join(str(issue) for issue in broken))
    write_root(built, output)
    return report


def start_time(path: str) -> datetime:
    points = [point for segment in segments_of(read_root(path)) for point in segment]
    if not points:
        raise SystemExit(f"{path}: no trackpoints")
    return point_time(points[0])
