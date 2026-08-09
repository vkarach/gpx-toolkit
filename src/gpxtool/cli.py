"""Command line front end."""

import argparse

from .config import RepairConfig
from .enrich import enrich
from .inspect import scan, tag_vs_distance
from .pipeline import merge_files


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


def cmd_merge(args: argparse.Namespace) -> None:
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
