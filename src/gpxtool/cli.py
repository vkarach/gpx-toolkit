"""Command line front end."""

import argparse
from pathlib import Path

from .config import NormalizeConfig, RepairConfig
from .enrich import enrich
from .gpxio import gpx_inputs, refuse_in_place
from .inspect import scan, tag_vs_distance
from .normalize import MISSING_HR_MODES, normalize_file
from .pipeline import merge_files
from .validate import WARNING, grouped


def _config_from_args(args: argparse.Namespace) -> RepairConfig:
    config = RepairConfig()
    for field in vars(config):
        value = getattr(args, field, None)
        if value is not None:
            setattr(config, field, value)
    return config


def _add_tuning_flags(parser: argparse.ArgumentParser) -> None:
    defaults = RepairConfig()
    for field, value in vars(defaults).items():
        parser.add_argument(f"--{field.replace('_', '-')}", type=type(value), default=None,
                            help=f"default {value}")


def _outputs_for(inputs: list[str], output: str | None, out_dir: str | None,
                 suffix: str) -> list[str]:
    """One explicit output path, or one derived name per input inside a directory."""
    if (output is None) == (out_dir is None):
        raise SystemExit("pass either -o for a single file or --out-dir for a batch")
    if output is not None:
        if len(inputs) > 1:
            raise SystemExit(f"-o takes one input, got {len(inputs)}; use --out-dir")
        return [output]
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    return [str(directory / f"{Path(source).stem}{suffix}.gpx") for source in inputs]


def cmd_normalize(args: argparse.Namespace) -> None:
    config = NormalizeConfig(
        gap_split_s=args.gap_split_s,
        missing_hr=args.missing_hr,
        speed_window_s=args.speed_window_s,
        elevation_smooth=args.elevation_smooth,
        derive_speed=not args.no_speed,
    )
    inputs = gpx_inputs(args.inputs)
    outputs = _outputs_for(inputs, args.output, args.out_dir, "_normalized")

    for source, target in zip(inputs, outputs):
        refuse_in_place([source], target)
        report = normalize_file(source, target, config)
        summary = report.summary
        print(f"{source} -> {target}")
        print(f"  {summary.points} pts, {summary.duration_s:.0f}s, "
              f"{summary.sample_rate_hz:.2f} Hz, "
              f"{summary.duplicate_share:.0%} held positions, "
              f"{summary.missing_hr} without hr")
        if report.metadata_fixed:
            print("  <metadate> rewritten as <metadata><time>")
        if report.exerciseinfo_moved:
            print("  <exerciseinfo> moved into <extensions>")
        if report.hr_filled:
            print(f"  {report.hr_filled} points forward-filled with the last hr")
        if report.points_dropped:
            print(f"  {report.points_dropped} trailing points without hr dropped")
        for at, span in summary.gaps:
            print(f"  gap at {at}: {span:.0f}s")
        print(f"  {report.segments} segment(s) written")
        for issue, count in grouped(report.issues):
            if issue.level == WARNING:
                print(f"  warning: {issue.message} ({count}x, first at {issue.where})")


def cmd_merge(args: argparse.Namespace) -> None:
    refuse_in_place(args.inputs, args.output)
    reports = merge_files(args.inputs, args.output, _config_from_args(args))
    print(args.output)
    for index, report in enumerate(reports):
        print(f"  seg {index}: {report.points} pts, {report.duration_s:.1f}s, "
              f"max {report.max_speed_kmh:.1f} km/h  [{report.source}]")
        print(f"    {report.duplicates_respaced} duplicates respaced, "
              f"{report.points_interpolated} points interpolated")
        for start, end, span in report.dropouts_ramped:
            print(f"    ramped dropout {start} -> {end} ({span:.1f}s)")


def cmd_scan(args: argparse.Namespace) -> None:
    for index, issues in enumerate(scan(args.input, args.accel_limit)):
        print(f"seg {index}: {len(issues)} anomalies")
        for issue in issues:
            print(f"  {issue.at}  {issue.kind:<11} {issue.detail}")


def cmd_compare(args: argparse.Namespace) -> None:
    print("time      tag_kmh  geo_kmh  dist_m")
    for stamp, tag, derived, distance in tag_vs_distance(args.input, args.segment, args.start, args.end):
        print(f"{stamp}  {tag:7.1f}  {derived:7.1f}  {distance:6.1f}")


def cmd_enrich(args: argparse.Namespace) -> None:
    refuse_in_place([args.main, args.secondary], args.output)
    fields = [name.strip() for name in args.fields.split(",")] if args.fields else None
    report = enrich(args.main, args.secondary, args.output, fields, args.shift_s,
                    args.max_gap_s, args.max_shift_s, not args.no_require_alignment)
    print(args.output)
    print(f"  donor: {report.donor_points} pts, {report.donor_span[0]} -> {report.donor_span[1]}")
    if report.alignment:
        found = report.alignment
        print(f"  matched stretch {found.donor_window[0]} -> {found.donor_window[1]} "
              f"at offset {found.offset_s:+.1f}s")
        print(f"  position error: median {found.median_error_m:.0f} m, p90 {found.p90_error_m:.0f} m, "
              f"coverage {found.coverage:.0%}")
    else:
        print(f"  offset forced to {report.applied_shift_s:+.1f}s")
    print(f"  overlap with main: {report.overlap_s:.0f}s")
    for name in report.fields:
        matched = report.matched[name]
        share = 100 * matched / report.main_points if report.main_points else 0
        print(f"  {name}: {matched}/{report.main_points} points ({share:.0f}%)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gpxtool", description="Repair and merge GPS tracks")
    sub = parser.add_subparsers(dest="command", required=True)
    defaults = NormalizeConfig()

    merge = sub.add_parser("merge", help="merge recordings into one repaired track")
    merge.add_argument("inputs", nargs="+")
    merge.add_argument("-o", "--output", required=True)
    _add_tuning_flags(merge)
    merge.set_defaults(func=cmd_merge)

    add = sub.add_parser("enrich", help="borrow sensor fields from a second recording")
    add.add_argument("main", help="authoritative track: geometry, timing, speed")
    add.add_argument("secondary", help="donor recording, e.g. a watch export with heart rate")
    add.add_argument("-o", "--output", required=True)
    add.add_argument("--fields", default="", help="comma separated, default every field the donor has")
    add.add_argument("--shift-s", type=float, default=None,
                     help="force a donor clock offset instead of matching trajectories")
    add.add_argument("--max-gap-s", type=float, default=10.0, help="do not interpolate across longer donor gaps")
    add.add_argument("--max-shift-s", type=float, default=900.0, help="widest clock offset to search")
    add.add_argument("--no-require-alignment", action="store_true",
                     help="enrich even when the trajectories do not match")
    add.set_defaults(func=cmd_enrich)

    fix = sub.add_parser("normalize", help="repair a Samsung Health export into valid GPX 1.1")
    fix.add_argument("inputs", nargs="+", help="GPX files or directories holding them")
    fix.add_argument("-o", "--output", help="output path for a single input")
    fix.add_argument("--out-dir", help="directory to write one file per input into")
    fix.add_argument("--gap-split-s", type=float, default=defaults.gap_split_s,
                     help=f"split into segments on longer pauses, default {defaults.gap_split_s}")
    fix.add_argument("--missing-hr", choices=MISSING_HR_MODES, default=defaults.missing_hr,
                     help=f"points without heart rate, default {defaults.missing_hr}")
    fix.add_argument("--speed-window-s", type=float, default=defaults.speed_window_s,
                     help=f"centred window for derived speed, default {defaults.speed_window_s}")
    fix.add_argument("--elevation-smooth", type=int, default=defaults.elevation_smooth,
                     help="points averaged either side of each elevation sample")
    fix.add_argument("--no-speed", action="store_true", help="do not derive <speed>")
    fix.set_defaults(func=cmd_normalize)

    check = sub.add_parser("scan", help="list duplicates, dropouts and impossible speed steps")
    check.add_argument("input")
    check.add_argument("--accel-limit", type=float, default=3.0)
    check.set_defaults(func=cmd_scan)

    compare = sub.add_parser("compare", help="recorded speed tag against speed from coordinates")
    compare.add_argument("input")
    compare.add_argument("--segment", type=int, default=0)
    compare.add_argument("--start", default="")
    compare.add_argument("--end", default="")
    compare.set_defaults(func=cmd_compare)

    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)
