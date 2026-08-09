"""Copy sensor channels from a donor recording onto an authoritative track.

The main file owns geometry, timing and speed. The secondary file only lends
fields the main one lacks - heart rate from a watch, cadence from a sensor -
matched by wall-clock time.
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import timedelta

from .align import AlignReport, find_offset
from .gpxio import (
    extension_names,
    extension_value,
    point_time,
    read_segments,
    set_extension_value,
    wall_time,
    write_gpx,
)

Points = list[ET.Element]


@dataclass
class EnrichReport:
    fields: list[str] = field(default_factory=list)
    donor_points: int = 0
    donor_span: tuple[str, str] = ("", "")
    main_points: int = 0
    matched: dict[str, int] = field(default_factory=dict)
    overlap_s: float = 0.0
    alignment: AlignReport | None = None
    applied_shift_s: float = 0.0


def _series(points: Points, name: str) -> list[tuple[float, float]]:
    """Donor samples as (epoch seconds, value), skipping unreadable entries."""
    samples = []
    for point in points:
        raw = extension_value(point, name)
        if raw is None:
            continue
        try:
            samples.append((point_time(point).timestamp(), float(raw)))
        except ValueError:
            continue
    return samples


def _sample_at(samples: list[tuple[float, float]], when: float, max_gap_s: float) -> float | None:
    """Linear interpolation between the surrounding donor samples."""
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
    if t1 - t0 > max_gap_s:
        return None
    if t1 == t0:
        return v0
    return v0 + (v1 - v0) * (when - t0) / (t1 - t0)


def _is_integral(samples: list[tuple[float, float]]) -> bool:
    return all(value.is_integer() for _, value in samples)


def enrich(main_path: str, secondary_path: str, output: str,
           fields: list[str] | None = None, shift_s: float | None = None,
           max_gap_s: float = 10.0, max_shift_s: float = 900.0,
           require_alignment: bool = True) -> EnrichReport:
    main_root, main_segments = read_segments(main_path)
    _, donor_segments = read_segments(secondary_path)

    donor: Points = [point for segment in donor_segments for point in segment]
    main_points = [point for segment in main_segments for point in segment]

    alignment = None
    if shift_s is None:
        alignment = find_offset(main_points, donor, max_shift_s)
        if not alignment.trusted and require_alignment:
            raise SystemExit(
                f"tracks do not line up: median error {alignment.median_error_m:.0f} m, "
                f"coverage {alignment.coverage:.0%} at best offset {alignment.offset_s:+.1f}s. "
                "Pass --shift-s to force an offset or --no-require-alignment to ignore."
            )
        shift_s = -alignment.offset_s

    if shift_s:
        for point in donor:
            element = point.find("{http://www.topografix.com/GPX/1/1}time")
            element.text = (point_time(point) + timedelta(seconds=shift_s)).strftime("%Y-%m-%dT%H:%M:%SZ")

    available = extension_names(donor)
    chosen = [name for name in (fields or available) if name in available]
    if not chosen:
        raise SystemExit(f"secondary has no usable fields (found: {available or 'none'})")

    series = {name: _series(donor, name) for name in chosen}
    integral = {name: _is_integral(samples) for name, samples in series.items()}

    report = EnrichReport(
        fields=chosen,
        donor_points=len(donor),
        donor_span=(point_time(donor[0]).strftime("%H:%M:%S"), point_time(donor[-1]).strftime("%H:%M:%S")),
        matched={name: 0 for name in chosen},
        alignment=alignment,
        applied_shift_s=shift_s,
    )
    report.main_points = len(main_points)

    for point in main_points:
        when = wall_time(point).timestamp()
        for name in chosen:
            value = _sample_at(series[name], when, max_gap_s)
            if value is None:
                continue
            set_extension_value(point, name, f"{value:.0f}" if integral[name] else f"{value:.2f}")
            report.matched[name] += 1

    if main_points:
        first, last = wall_time(main_points[0]), wall_time(main_points[-1])
        donor_first, donor_last = point_time(donor[0]), point_time(donor[-1])
        start, end = max(first, donor_first), min(last, donor_last)
        report.overlap_s = max(0.0, (end - start).total_seconds())

    write_gpx(main_root, main_segments, output, f"gpx-toolkit enrich: {main_path} + {secondary_path}")
    return report
