"""Tunables for the repair and normalise pipelines."""

from dataclasses import dataclass


@dataclass
class RepairConfig:
    fill_max_gap_s: float = 5.0
    """Dropouts up to this long get 1 Hz interpolated points."""

    speed_window_s: float = 5.0
    """Centred time span used to derive speed from travelled distance, sane range 3-7 s."""

    speed_smooth: int = 1
    """Points averaged either side of each sample after deriving speed."""

    elevation_smooth: int = 2
    """Points averaged either side of each sample for elevation."""

    reliable_speed: float = 5.5
    """m/s at which the receiver is considered locked again after a dropout."""

    frozen_speed: float = 2.0
    """m/s below which a post-dropout reading counts as a stuck position."""

    frozen_min_s: float = 2.0
    """A dropout is only repaired if the receiver stayed stuck at least this long."""

    max_ramp_s: float = 12.0
    """Never stretch a repaired acceleration ramp longer than this."""

    accel_max: float = 2.5
    """m/s^2 ceiling applied to speed increases."""

    decel_max: float = 4.5
    """m/s^2 ceiling applied to speed decreases."""

    stitch_gap_s: float = 1.0
    """Spacing inserted between merged segments."""


@dataclass
class NormalizeConfig:
    gap_split_s: float = 10.0
    """Longer holes end the current trkseg, so nothing interpolates across a pause."""

    missing_hr: str = "fill"
    """fill forward from the last reading, drop the tail, or keep it as exported."""

    speed_window_s: float = 5.0
    """Centred time span used to derive speed, sane range 3-7 s."""

    elevation_smooth: int = 0
    """Points averaged either side of each sample before gradient is read off."""

    derive_speed: bool = True
    """Samsung stores no instantaneous speed, so it is computed unless disabled."""
