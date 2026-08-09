"""Tunables for the repair pipeline."""

from dataclasses import dataclass


@dataclass
class RepairConfig:
    fill_max_gap_s: float = 5.0
    """Dropouts up to this long get 1 Hz interpolated points."""

    speed_window_s: float = 2.0
    """Centred time span used to derive speed from travelled distance."""

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
