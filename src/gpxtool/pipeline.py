"""Merge several GPX recordings into one continuous, repaired track."""

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import timedelta

from .config import RepairConfig
from .gpxio import point_time, read_segments, set_point_time, stamp_source_time, write_gpx
from .repair import (
    clamp_acceleration,
    dedupe_timestamps,
    fill_gaps,
    ramp_over_dropouts,
    recompute_speed,
)


@dataclass
class SegmentReport:
    source: str
    points: int
    duration_s: float
    max_speed_kmh: float
    duplicates_respaced: int = 0
    points_interpolated: int = 0
    dropouts_ramped: list[tuple[str, str, float]] = field(default_factory=list)


def repair_segment(points: list[ET.Element], config: RepairConfig) -> tuple[list[ET.Element], SegmentReport]:
    for point in points:
        stamp_source_time(point)
    duplicates = dedupe_timestamps(points)
    points, added = fill_gaps(points, config)
    for point in points:
        stamp_source_time(point)
    recompute_speed(points, config)
    ramped = ramp_over_dropouts(points, config)
    clamp_acceleration(points, config)

    duration = (point_time(points[-1]) - point_time(points[0])).total_seconds()
    fastest = max(float(p.find("{http://www.topografix.com/GPX/1/1}speed").text) for p in points)
    report = SegmentReport(
        source="",
        points=len(points),
        duration_s=duration,
        max_speed_kmh=fastest * 3.6,
        duplicates_respaced=duplicates,
        points_interpolated=added,
        dropouts_ramped=ramped,
    )
    return points, report


def stitch(segments: list[list[ET.Element]], config: RepairConfig) -> None:
    """Shift each segment so it starts right after the previous one ends."""
    end = point_time(segments[0][-1])
    for points in segments[1:]:
        shift = point_time(points[0]) - (end + timedelta(seconds=config.stitch_gap_s))
        for point in points:
            set_point_time(point, point_time(point) - shift)
        end = point_time(points[-1])


def merge_files(paths: list[str], output: str, config: RepairConfig) -> list[SegmentReport]:
    root, _ = read_segments(paths[0])

    segments: list[list[ET.Element]] = []
    reports: list[SegmentReport] = []
    for path in paths:
        _, file_segments = read_segments(path)
        for points in file_segments:
            repaired, report = repair_segment(points, config)
            report.source = path
            segments.append(repaired)
            reports.append(report)

    stitch(segments, config)
    write_gpx(root, segments, output, "gpx-toolkit merge: " + ", ".join(paths))
    return reports
