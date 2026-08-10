"""Cut a track down to the stretch a video actually covers."""

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta

from .gpxio import build_gpx, point_time, read_root, segments_of, write_root
from .validate import ERROR, Issue, validate


@dataclass
class ClipReport:
    start: datetime
    end: datetime
    kept: int
    dropped: int
    segments: int
    lead_in_s: float
    lead_out_s: float
    issues: list[Issue]

    @property
    def span_s(self) -> float:
        return (self.end - self.start).total_seconds()


def clip(path: str, output: str, start: datetime, end: datetime, pad_s: float = 0.0) -> ClipReport:
    """Keep the points between start and end, padded either side, and renumber nothing."""
    root = read_root(path)
    segments = segments_of(root)
    total = sum(len(segment) for segment in segments)

    first = start - timedelta(seconds=pad_s)
    last = end + timedelta(seconds=pad_s)
    kept: list[list[ET.Element]] = []
    for segment in segments:
        inside = [point for point in segment if first <= point_time(point) <= last]
        if inside:
            kept.append(inside)

    points = [point for segment in kept for point in segment]
    if not points:
        raise SystemExit(f"{path}: no trackpoints between {start:%H:%M:%S} and {end:%H:%M:%S}")

    built = build_gpx(root, kept, f"gpx-toolkit clip: {path}")
    issues = validate(built)
    broken = [issue for issue in issues if issue.level == ERROR]
    if broken:
        raise SystemExit(f"{path} would not produce valid GPX 1.1, nothing written:\n  " +
                         "\n  ".join(str(issue) for issue in broken))
    write_root(built, output)

    return ClipReport(
        start=point_time(points[0]),
        end=point_time(points[-1]),
        kept=len(points),
        dropped=total - len(points),
        segments=len(kept),
        lead_in_s=(point_time(points[0]) - start).total_seconds(),
        lead_out_s=(end - point_time(points[-1])).total_seconds(),
        issues=issues,
    )
