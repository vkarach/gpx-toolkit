"""Structural check against the GPX 1.1 content model, without a schema library.

Covers what a real export gets wrong: unknown or misordered elements, missing
attributes, unparseable timestamps. `<speed>` survives as a warning because it
is GPX 1.0 vocabulary that overlay tools still read.
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass

from .gpxio import NS, parse_time, q

ERROR = "error"
WARNING = "warning"

LEGACY_TAGS = {"speed", "course"}

SEQUENCES: dict[str, list[str]] = {
    "gpx": ["metadata", "wpt", "rte", "trk", "extensions"],
    "metadata": ["name", "desc", "author", "copyright", "link", "time", "keywords", "bounds",
                 "extensions"],
    "trk": ["name", "cmt", "desc", "src", "link", "number", "type", "extensions", "trkseg"],
    "trkseg": ["trkpt", "extensions"],
    "trkpt": ["ele", "time", "magvar", "geoidheight", "name", "cmt", "desc", "src", "link", "sym",
              "type", "fix", "sat", "hdop", "vdop", "pdop", "ageofdgpsdata", "dgpsid",
              "extensions"],
}


@dataclass
class Issue:
    level: str
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.level}: {self.where}: {self.message}"


def _local(tag: str) -> str:
    return tag.split("}")[-1]


def _in_gpx_ns(tag: str) -> bool:
    return tag.startswith(f"{{{NS}}}")


def _check_children(element: ET.Element, where: str, issues: list[Issue]) -> None:
    order = SEQUENCES[_local(element.tag)]
    position = -1
    for index, child in enumerate(element):
        name = _local(child.tag)
        spot = f"{where}/{name}[{index}]"
        if not _in_gpx_ns(child.tag):
            issues.append(Issue(ERROR, spot, "foreign namespace outside <extensions>"))
            continue
        if name in LEGACY_TAGS:
            issues.append(Issue(WARNING, spot, "GPX 1.0 element kept for overlay tools"))
            continue
        if name not in order:
            issues.append(Issue(ERROR, spot, f"not allowed inside <{_local(element.tag)}>"))
            continue
        rank = order.index(name)
        if rank < position:
            issues.append(Issue(ERROR, spot, f"must come before <{order[position]}>"))
        position = max(position, rank)
        if name in SEQUENCES:
            _check_children(child, spot, issues)


def _check_trkpt(point: ET.Element, where: str, issues: list[Issue]) -> None:
    for axis, limit in (("lat", 90.0), ("lon", 180.0)):
        raw = point.get(axis)
        if raw is None:
            issues.append(Issue(ERROR, where, f"missing {axis}"))
            continue
        try:
            value = float(raw)
        except ValueError:
            issues.append(Issue(ERROR, where, f"{axis} is not a number: {raw!r}"))
            continue
        if abs(value) > limit:
            issues.append(Issue(ERROR, where, f"{axis} out of range: {value}"))

    stamp = point.find(q("time"))
    if stamp is None or not stamp.text:
        issues.append(Issue(WARNING, where, "no <time>, speed cannot be derived"))
        return
    try:
        parse_time(stamp.text)
    except ValueError:
        issues.append(Issue(ERROR, where, f"unparseable time: {stamp.text!r}"))


def validate(root: ET.Element) -> list[Issue]:
    """Every deviation from GPX 1.1 found in the tree, worst kind first."""
    issues: list[Issue] = []
    if _local(root.tag) != "gpx" or not _in_gpx_ns(root.tag):
        return [Issue(ERROR, "/", f"root is {root.tag}, expected a GPX 1.1 <gpx>")]
    if root.get("version") != "1.1":
        issues.append(Issue(ERROR, "gpx", f"version is {root.get('version')!r}, expected '1.1'"))
    if not root.get("creator"):
        issues.append(Issue(ERROR, "gpx", "missing creator"))

    _check_children(root, "gpx", issues)

    for track_index, track in enumerate(root.findall(q("trk"))):
        for segment_index, segment in enumerate(track.findall(q("trkseg"))):
            base = f"gpx/trk[{track_index}]/trkseg[{segment_index}]"
            for point_index, point in enumerate(segment.findall(q("trkpt"))):
                _check_trkpt(point, f"{base}/trkpt[{point_index}]", issues)

    issues.sort(key=lambda issue: issue.level != ERROR)
    return issues


def errors(issues: list[Issue]) -> list[Issue]:
    return [issue for issue in issues if issue.level == ERROR]


def grouped(issues: list[Issue]) -> list[tuple[Issue, int]]:
    """One entry per distinct complaint, since a bad trkpt is usually every trkpt."""
    counts: dict[tuple[str, str], list] = {}
    for issue in issues:
        key = (issue.level, issue.message)
        if key in counts:
            counts[key][1] += 1
        else:
            counts[key] = [issue, 1]
    return [(issue, count) for issue, count in counts.values()]
