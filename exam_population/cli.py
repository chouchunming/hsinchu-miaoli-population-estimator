from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .service import analyze, check_gaps, default_data_root, update


def parse_roc_month(value: str) -> tuple[int, int]:
    try:
        year_text, month_text = value.split("-", 1)
        year, month = int(year_text), int(month_text)
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError("格式必須為民國 YYY-MM") from exc
    if year < 1 or not 1 <= month <= 12:
        raise argparse.ArgumentTypeError("民國年月超出有效範圍")
    return year, month


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="新竹縣、新竹市、苗栗縣戶籍人口 cohort 分析"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("update", "analyze"):
        child = subparsers.add_parser(command)
        child.add_argument("--data-root", type=Path, default=default_data_root())
        child.add_argument("--start-year", type=int, default=116)
        child.add_argument("--end-year", type=int, default=130)
        child.add_argument(
            "--backfill-from", type=parse_roc_month, default=(114, 1)
        )
        if command == "update":
            child.add_argument(
                "--range-end",
                type=parse_roc_month,
                default=None,
                help=argparse.SUPPRESS,
            )
    gaps = subparsers.add_parser("gaps")
    gaps.add_argument("--data-root", type=Path, default=default_data_root())
    gaps.add_argument("--backfill-from", type=parse_roc_month, default=(114, 1))
    return parser


def main(argv=None, *, sources=None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.command == "update":
        result = update(
            arguments.data_root,
            sources=sources,
            start_year=arguments.start_year,
            end_year=arguments.end_year,
            backfill_from=arguments.backfill_from,
            range_end=arguments.range_end,
        )
        for warning in result.warnings:
            print(f"WARNING: {warning}", file=sys.stderr)
        if result.export_dir is not None:
            print(result.export_dir)
        return 1 if result.status == "failed" else 0
    if arguments.command == "analyze":
        result = analyze(
            arguments.data_root,
            start_year=arguments.start_year,
            end_year=arguments.end_year,
            backfill_from=arguments.backfill_from,
        )
        for warning in result.warnings:
            print(f"WARNING: {warning}", file=sys.stderr)
        print(result.export_dir)
        return 0
    report = check_gaps(
        arguments.data_root,
        backfill_from=arguments.backfill_from,
    )
    for item in report.missing:
        print(
            f"MISSING {item.region} {item.dataset} "
            f"{item.roc_year}-{item.month:02d}"
        )
    for item in report.unsupported_media:
        print(
            f"UNSUPPORTED {item.region} {item.dataset} "
            f"{item.roc_year}-{item.month:02d}"
        )
    return 0
