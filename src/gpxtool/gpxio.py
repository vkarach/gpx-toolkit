"""GPX parsing and serialisation helpers."""

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

NS = "http://www.topografix.com/GPX/1/1"
TPX = "http://www.garmin.com/xmlschemas/TrackPointExtension/v1"
SOURCE_TIME_ATTR = "srctime"
TPX_ORDER = ["atemp", "wtemp", "depth", "hr", "cad"]

ET.register_namespace("", NS)
ET.register_namespace("gpxtpx", TPX)


def q(tag: str) -> str:
    return f"{{{NS}}}{tag}"


def tpx(tag: str) -> str:
    return f"{{{TPX}}}{tag}"


def parse_time(text: str) -> datetime:
    text = text.rstrip("Z")
    fmt = "%Y-%m-%dT%H:%M:%S.%f" if "." in text else "%Y-%m-%dT%H:%M:%S"
    return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)


def format_time(value: datetime) -> str:
    if value.microsecond:
        return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def point_time(point: ET.Element) -> datetime:
    return parse_time(point.find(q("time")).text)


def set_point_time(point: ET.Element, value: datetime) -> None:
    point.find(q("time")).text = format_time(value)


def point_float(point: ET.Element, tag: str, default: float = 0.0) -> float:
    element = point.find(q(tag))
    return float(element.text) if element is not None else default


def set_point_float(point: ET.Element, tag: str, value: float, digits: int = 3) -> None:
    element = point.find(q(tag))
    if element is None:
        element = ET.Element(q(tag))
        extensions = point.find(q("extensions"))
        point.insert(list(point).index(extensions) if extensions is not None else len(point), element)
    element.text = f"{value:.{digits}f}"


def point_latlon(point: ET.Element) -> tuple[float, float]:
    return float(point.get("lat")), float(point.get("lon"))


def make_point(lat: float, lon: float, ele: float, time: datetime, speed: float = 0.0) -> ET.Element:
    point = ET.Element(q("trkpt"), {"lat": f"{lat:.7f}", "lon": f"{lon:.7f}"})
    ET.SubElement(point, q("ele")).text = f"{ele:.2f}"
    ET.SubElement(point, q("time")).text = format_time(time)
    ET.SubElement(point, q("speed")).text = f"{speed:.3f}"
    point.set("interpolated", "true")
    return point


def is_synthetic(point: ET.Element) -> bool:
    return point.get("interpolated") == "true"


def stamp_source_time(point: ET.Element) -> None:
    """Remember the wall clock before any shifting, so donors can still align."""
    if point.get(SOURCE_TIME_ATTR) is None:
        point.set(SOURCE_TIME_ATTR, point.find(q("time")).text)


def wall_time(point: ET.Element):
    """Original recording time, even after the point was shifted by a merge."""
    return parse_time(point.get(SOURCE_TIME_ATTR) or point.find(q("time")).text)


def extension_value(point: ET.Element, name: str) -> str | None:
    element = point.find(f"./{q('extensions')}/{tpx('TrackPointExtension')}/{tpx(name)}")
    return element.text if element is not None else None


def _tpx_rank(child: ET.Element) -> int:
    name = child.tag.split("}")[-1]
    return TPX_ORDER.index(name) if name in TPX_ORDER else len(TPX_ORDER)


def set_extension_value(point: ET.Element, name: str, text: str) -> None:
    extensions = point.find(q("extensions"))
    if extensions is None:
        extensions = ET.SubElement(point, q("extensions"))
    container = extensions.find(tpx("TrackPointExtension"))
    if container is None:
        container = ET.SubElement(extensions, tpx("TrackPointExtension"))
    element = container.find(tpx(name))
    if element is None:
        element = ET.SubElement(container, tpx(name))
        container[:] = sorted(container, key=_tpx_rank)
    element.text = text


def extension_names(points: list[ET.Element]) -> list[str]:
    """Distinct TrackPointExtension fields present in a track."""
    names: list[str] = []
    for point in points:
        container = point.find(f"./{q('extensions')}/{tpx('TrackPointExtension')}")
        if container is None:
            continue
        for child in container:
            name = child.tag.split("}")[-1]
            if name not in names:
                names.append(name)
    return names


def read_root(path: str) -> ET.Element:
    return ET.parse(path).getroot()


def segments_of(root: ET.Element) -> list[list[ET.Element]]:
    segments = []
    for track in root.findall(q("trk")):
        for segment in track.findall(q("trkseg")):
            points = segment.findall(q("trkpt"))
            if points:
                segments.append(points)
    return segments


def read_segments(path: str) -> tuple[ET.Element, list[list[ET.Element]]]:
    """Return the gpx root and every track segment as a list of points."""
    root = read_root(path)
    return root, segments_of(root)


def track_name(root: ET.Element) -> str | None:
    track = root.find(q("trk"))
    if track is None:
        return None
    name = track.find(q("name"))
    return name.text if name is not None else None


def order_root(root: ET.Element) -> None:
    """GPX 1.1 wants metadata first and extensions last; Samsung leaves them anywhere."""
    rank = {"metadata": 0, "wpt": 1, "rte": 2, "trk": 3, "extensions": 4}
    children = sorted(root, key=lambda child: rank.get(child.tag.split("}")[-1], 3))
    for child in list(root):
        root.remove(child)
    root.extend(children)


def build_gpx(root: ET.Element, segments: list[list[ET.Element]], creator: str) -> ET.Element:
    """Rebuild the track from the given segments, leaving the rest of the file alone."""
    name = track_name(root)
    for track in root.findall(q("trk")):
        root.remove(track)
    track = ET.SubElement(root, q("trk"))
    if name:
        ET.SubElement(track, q("name")).text = name
    for points in segments:
        segment = ET.SubElement(track, q("trkseg"))
        for point in points:
            segment.append(point)
    root.set("creator", creator)
    order_root(root)
    return root


def write_root(root: ET.Element, path: str) -> None:
    ET.indent(root, space="    ")
    ET.ElementTree(root).write(path, xml_declaration=True, encoding="UTF-8")


def write_gpx(root: ET.Element, segments: list[list[ET.Element]], path: str, creator: str) -> None:
    write_root(build_gpx(root, segments, creator), path)


def gpx_inputs(paths: list[str]) -> list[str]:
    """Expand directories into the GPX files they hold, keeping the given order."""
    found: list[str] = []
    for entry in paths:
        item = Path(entry)
        if item.is_dir():
            found.extend(str(child) for child in sorted(item.iterdir())
                         if child.suffix.lower() == ".gpx")
        else:
            found.append(str(item))
    if not found:
        raise SystemExit(f"no GPX files in {', '.join(paths)}")
    return found


def refuse_in_place(inputs: list[str], output: str) -> None:
    target = Path(output).resolve()
    if any(Path(source).resolve() == target for source in inputs):
        raise SystemExit(f"refusing to overwrite the input file: {output}")
