"""Line a video up with a track, without trusting the camera's clock blindly.

The MP4 spec calls mvhd creation_time the moment the movie was created, but
plenty of phones and action cams stamp it when the file is closed instead. The
two readings put the video at opposite ends of its own duration, so the fit is
scored against the track rather than assumed.
"""

import struct
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .geo import haversine
from .gpxio import point_float, point_latlon, point_time, read_root, segments_of

MP4_EPOCH = datetime(1904, 1, 1, tzinfo=timezone.utc)
CONTAINERS = {b"moov", b"trak", b"mdia"}
STAMPS = ("auto", "start", "end")


@dataclass
class VideoTimes:
    created: datetime
    duration_s: float


@dataclass
class Fit:
    stamp: str
    start: datetime
    end: datetime
    distance_m: float
    coverage: float
    ascent_m: float

    @property
    def average_kmh(self) -> float:
        span = (self.end - self.start).total_seconds()
        return self.distance_m / span * 3.6 if span > 0 else 0.0


@dataclass
class SyncReport:
    video: str
    track: str
    duration_s: float
    track_start: datetime
    track_duration_s: float
    chosen: Fit
    candidates: list[Fit] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def offset_s(self) -> float:
        return (self.chosen.start - self.track_start).total_seconds()


def parse_timezone(text: str) -> timezone:
    """Accept +02:00, -0530, UTC or an IANA name; never guess from the host clock."""
    value = text.strip()
    if value.upper() in ("UTC", "Z", "GMT"):
        return timezone.utc
    if value[0] in "+-":
        digits = value[1:].replace(":", "")
        if not digits.isdigit() or len(digits) not in (2, 4):
            raise SystemExit(f"unreadable timezone {text!r}, expected a form like +02:00")
        hours, minutes = int(digits[:2]), int(digits[2:] or 0)
        delta = timedelta(hours=hours, minutes=minutes)
        return timezone(-delta if value[0] == "-" else delta)
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(value)
    except Exception as error:
        raise SystemExit(f"unknown timezone {text!r}: {error}. Pass a fixed offset like +02:00.")


def _walk_boxes(stream, end: int):
    """Yield (type, payload start, payload end) for every box between here and end."""
    while stream.tell() + 8 <= end:
        start = stream.tell()
        header = stream.read(8)
        if len(header) < 8:
            return
        size, kind = struct.unpack(">I4s", header)
        if size == 1:
            size = struct.unpack(">Q", stream.read(8))[0]
            body = stream.tell()
        elif size == 0:
            size, body = end - start, stream.tell()
        else:
            body = stream.tell()
        if size < 8:
            return
        yield kind, body, start + size
        stream.seek(start + size)


def _find_mvhd(stream, end: int) -> bytes | None:
    for kind, body, stop in _walk_boxes(stream, end):
        if kind == b"mvhd":
            stream.seek(body)
            return stream.read(stop - body)
        if kind in CONTAINERS:
            stream.seek(body)
            found = _find_mvhd(stream, stop)
            if found is not None:
                return found
    return None


def read_mvhd(path: str) -> VideoTimes:
    """Creation time as the camera wrote it, with no timezone attached, and the runtime."""
    with open(path, "rb") as stream:
        stream.seek(0, 2)
        size = stream.tell()
        stream.seek(0)
        payload = _find_mvhd(stream, size)

    if payload is None:
        raise SystemExit(f"{path}: no mvhd box, this does not look like an MP4")

    wide = payload[0] == 1
    if len(payload) < (32 if wide else 20):
        raise SystemExit(f"{path}: mvhd box is truncated")
    if wide:
        seconds = struct.unpack(">Q", payload[4:12])[0]
        scale, ticks = struct.unpack(">IQ", payload[20:32])
    else:
        seconds = struct.unpack(">I", payload[4:8])[0]
        scale, ticks = struct.unpack(">II", payload[12:20])
    if seconds == 0:
        raise SystemExit(f"{path}: mvhd creation time is zero, the camera wrote no timestamp")
    if not scale:
        raise SystemExit(f"{path}: mvhd timescale is zero, the duration is unreadable")
    return VideoTimes(MP4_EPOCH + timedelta(seconds=seconds), ticks / scale)


def creation_time(path: str) -> datetime:
    return read_mvhd(path).created


def stamped_utc(times: VideoTimes, camera_tz: timezone) -> datetime:
    """Reinterpret the camera's naive timestamp in the timezone it was set to."""
    return times.created.replace(tzinfo=None).replace(tzinfo=camera_tz).astimezone(timezone.utc)


def human_offset(seconds: float) -> str:
    sign = "-" if seconds < 0 else "+"
    hours, rest = divmod(abs(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{sign}{int(hours):d}:{int(minutes):02d}:{secs:04.1f}"


def _measure(points: list, start: datetime, end: datetime, stamp: str) -> Fit:
    """What the track did during a candidate window: travelled, covered, climbed."""
    inside = [point for point in points if start <= point_time(point) <= end]
    span = (end - start).total_seconds()
    if len(inside) < 2:
        return Fit(stamp, start, end, 0.0, 0.0, 0.0)

    distance = sum(haversine(*point_latlon(a), *point_latlon(b))
                   for a, b in zip(inside, inside[1:]))
    elevations = [point_float(point, "ele") for point in inside]
    ascent = sum(max(0.0, b - a) for a, b in zip(elevations, elevations[1:]))
    covered = (point_time(inside[-1]) - point_time(inside[0])).total_seconds()
    return Fit(stamp, start, end, distance, covered / span if span > 0 else 0.0, ascent)


def candidates(times: VideoTimes, camera_tz: timezone, points: list) -> list[Fit]:
    """The video read as starting at the mvhd stamp, and as ending there."""
    stamped = stamped_utc(times, camera_tz)
    length = timedelta(seconds=times.duration_s)
    return [
        _measure(points, stamped, stamped + length, "start"),
        _measure(points, stamped - length, stamped, "end"),
    ]


def sync(track: str, video: str, camera_tz: timezone, stamp: str = "auto") -> SyncReport:
    if stamp not in STAMPS:
        raise SystemExit(f"unknown --stamp {stamp!r}, expected one of {', '.join(STAMPS)}")

    points = [point for segment in segments_of(read_root(track)) for point in segment]
    if not points:
        raise SystemExit(f"{track}: no trackpoints")

    times = read_mvhd(video)
    fits = candidates(times, camera_tz, points)
    if stamp == "auto":
        chosen = max(fits, key=lambda fit: (fit.coverage > 0.5, fit.distance_m))
    else:
        chosen = next(fit for fit in fits if fit.stamp == stamp)

    start = point_time(points[0])
    track_span = (point_time(points[-1]) - start).total_seconds()
    report = SyncReport(video, track, times.duration_s, start, track_span, chosen, fits)

    other = next(fit for fit in fits if fit.stamp != chosen.stamp)
    if stamp == "auto" and other.distance_m > chosen.distance_m:
        report.warnings.append(
            f"reading the stamp as the {other.stamp} of the video covers more ground "
            f"({other.distance_m:.0f} m vs {chosen.distance_m:.0f} m); force it with --stamp")
    if chosen.coverage < 0.5:
        report.warnings.append(
            f"the track only covers {chosen.coverage:.0%} of the video window: clock drift, a "
            "wrong --camera-tz, or the wrong pair of files")
    if report.offset_s < 0:
        report.warnings.append(
            f"video starts {human_offset(report.offset_s)} before the track")
    elif report.offset_s > track_span:
        report.warnings.append(
            f"video starts {human_offset(report.offset_s)} in, past the track's own "
            f"{human_offset(track_span)}: the two files do not overlap")
    return report
