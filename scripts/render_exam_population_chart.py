#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import html
import math
from pathlib import Path
import sys
import tempfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
START_YEAR = 115
END_YEAR = 130
EXAM_FILENAME = f"exam_population_{START_YEAR}_{END_YEAR}.csv"
SVG_FILENAME = f"exam-population-{START_YEAR}-{END_YEAR}.svg"
REQUIRED_FIELDS = (
    "會考年度",
    "新竹縣預估人數",
    "新竹市預估人數",
    "苗栗縣預估人數",
    "三區合計",
    "資料完整性",
)


class ChartDataError(ValueError):
    pass


@dataclass(frozen=True)
class ChartRow:
    exam_year: int
    hsinchu_county: int
    hsinchu_city: int
    miaoli_county: int
    total: int
    completeness: str

    @property
    def regional_values(self) -> tuple[int, int, int]:
        return (self.hsinchu_county, self.hsinchu_city, self.miaoli_county)

    @property
    def provisional(self) -> bool:
        return "暫估" in self.completeness


def find_latest_exam_csv(data_root: Path) -> Path:
    candidates = sorted(Path(data_root).glob(f"exports/*/{EXAM_FILENAME}"))
    if not candidates:
        raise ChartDataError(f"找不到會考人口 CSV：{data_root}")
    return candidates[-1]


def _non_negative_integer(raw: dict, field: str, line_number: int) -> int:
    try:
        value = int((raw.get(field) or "").strip())
    except ValueError as exc:
        raise ChartDataError(f"第 {line_number} 列 {field} 不是整數") from exc
    if value < 0:
        raise ChartDataError(f"第 {line_number} 列 {field} 不得為負數")
    return value


def load_chart_rows(csv_path: Path) -> tuple[ChartRow, ...]:
    try:
        stream = Path(csv_path).open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise ChartDataError(f"無法讀取會考人口 CSV：{csv_path}：{exc}") from exc

    with stream:
        reader = csv.DictReader(stream)
        fieldnames = tuple(reader.fieldnames or ())
        missing = tuple(
            field for field in REQUIRED_FIELDS if field not in fieldnames
        )
        if missing:
            raise ChartDataError(f"缺少必要欄位：{', '.join(missing)}")

        rows = []
        for line_number, raw in enumerate(reader, start=2):
            row = ChartRow(
                exam_year=_non_negative_integer(raw, "會考年度", line_number),
                hsinchu_county=_non_negative_integer(
                    raw,
                    "新竹縣預估人數",
                    line_number,
                ),
                hsinchu_city=_non_negative_integer(
                    raw,
                    "新竹市預估人數",
                    line_number,
                ),
                miaoli_county=_non_negative_integer(
                    raw,
                    "苗栗縣預估人數",
                    line_number,
                ),
                total=_non_negative_integer(raw, "三區合計", line_number),
                completeness=(raw.get("資料完整性") or "").strip(),
            )
            if row.total != sum(row.regional_values):
                raise ChartDataError(
                    f"第 {line_number} 列三區合計不等於三地加總"
                )
            rows.append(row)

    expected_years = tuple(range(START_YEAR, END_YEAR + 1))
    if tuple(row.exam_year for row in rows) != expected_years:
        raise ChartDataError(
            f"會考年度必須連續且恰為 {START_YEAR}–{END_YEAR}"
        )
    if any(row.provisional for row in rows[:-1]) or not rows[-1].provisional:
        raise ChartDataError("只有民國 130 年必須標示為暫估")
    return tuple(rows)


SERIES = (
    ("hsinchu-county", "新竹縣", "hsinchu_county", "#0072B2", ""),
    ("hsinchu-city", "新竹市", "hsinchu_city", "#D55E00", "9 5"),
    ("miaoli-county", "苗栗縣", "miaoli_county", "#009E73", "3 4"),
    ("total", "三區合計", "total", "#7A3E9D", ""),
)


def render_svg(rows: tuple[ChartRow, ...]) -> str:
    width, height = 1600, 980
    left, right, top, bottom = 130, 70, 170, 135
    plot_width = width - left - right
    plot_height = height - top - bottom
    y_step = 2_000
    y_max = max(
        y_step,
        math.ceil(max(row.total for row in rows) / y_step) * y_step,
    )

    def x_position(index: int) -> float:
        return left + index * plot_width / (len(rows) - 1)

    def y_position(value: int) -> float:
        return top + plot_height * (1 - value / y_max)

    parts = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {width} {height}" role="img" '
            f'aria-labelledby="chart-title chart-desc">'
        ),
        '<title id="chart-title">竹竹苗國三會考應屆人口推估</title>',
        (
            '<desc id="chart-desc">民國 115 至 130 年新竹縣、新竹市、'
            '苗栗縣與三區合計的戶籍人口 cohort 推估；民國 130 年為暫估。'
            "</desc>"
        ),
        """<style>
text { font-family: "PingFang TC", "Noto Sans TC", sans-serif; fill: #25313c; }
.grid { stroke: #d7dee5; stroke-width: 1; }
.axis { stroke: #52606d; stroke-width: 2; }
.tick { font-size: 18px; fill: #52606d; }
.value { font-size: 15px; font-weight: 500; paint-order: stroke;
         stroke: #fff; stroke-width: 4px; stroke-linejoin: round; }
.legend { font-size: 21px; font-weight: 500; }
.note { font-size: 18px; fill: #52606d; }
</style>""",
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        '<text x="80" y="60" font-size="34" font-weight="500">'
        "竹竹苗國三會考應屆人口推估</text>",
        '<text x="80" y="100" class="note">'
        "單歲戶籍人口按出生月份重疊比例加權；非實際畢業或報考人數</text>",
    ]
    for value in range(0, y_max + y_step, y_step):
        y = y_position(value)
        parts.extend(
            (
                f'<line class="grid" x1="{left}" y1="{y:.1f}" '
                f'x2="{width - right}" y2="{y:.1f}"/>',
                f'<text class="tick" x="{left - 18}" y="{y + 6:.1f}" '
                f'text-anchor="end">{value:,}</text>',
            )
        )
    parts.extend(
        (
            f'<line class="axis" x1="{left}" y1="{top}" x2="{left}" '
            f'y2="{height - bottom}"/>',
            f'<line class="axis" x1="{left}" y1="{height - bottom}" '
            f'x2="{width - right}" y2="{height - bottom}"/>',
            '<text class="tick" x="28" y="510" transform="rotate(-90 28 510)">'
            "預估人數（人）</text>",
            f'<text class="tick" x="{width / 2}" y="{height - 35}" '
            f'text-anchor="middle">民國會考年度</text>',
        )
    )
    for index, row in enumerate(rows):
        x = x_position(index)
        parts.extend(
            (
                f'<line class="grid" x1="{x:.1f}" y1="{top}" '
                f'x2="{x:.1f}" y2="{height - bottom}"/>',
                f'<text class="tick" x="{x:.1f}" y="{height - bottom + 34}" '
                f'text-anchor="middle">{row.exam_year}</text>',
            )
        )

    label_offsets = (-12, 21, 38, -18)
    for series_index, (slug, _label, attribute, color, dash) in enumerate(
        SERIES
    ):
        values = tuple(getattr(row, attribute) for row in rows)
        points = " ".join(
            f"{x_position(index):.1f},{y_position(value):.1f}"
            for index, value in enumerate(values)
        )
        stroke_width = 5 if slug == "total" else 3
        dash_attribute = f' stroke-dasharray="{dash}"' if dash else ""
        parts.append(
            f'<polyline data-series-line="{slug}" points="{points}" '
            f'fill="none" stroke="{color}" stroke-width="{stroke_width}"'
            f'{dash_attribute}/>'
        )
        for index, (row, value) in enumerate(zip(rows, values, strict=True)):
            x, y = x_position(index), y_position(value)
            provisional = str(row.provisional).lower()
            fill = "#ffffff" if row.provisional else color
            radius = 7 if slug == "total" else 5
            parts.extend(
                (
                    f'<circle data-series="{slug}" '
                    f'data-year="{row.exam_year}" '
                    f'data-provisional="{provisional}" '
                    f'cx="{x:.1f}" cy="{y:.1f}" r="{radius}" '
                    f'fill="{fill}" stroke="{color}" stroke-width="3"/>',
                    f'<text class="value" x="{x:.1f}" '
                    f'y="{y + label_offsets[series_index]:.1f}" '
                    f'text-anchor="middle">{value:,}</text>',
                )
            )

    legend_x = 750
    for index, (slug, label, _attribute, color, dash) in enumerate(SERIES):
        x = legend_x + index * 190
        dash_attribute = f' stroke-dasharray="{dash}"' if dash else ""
        stroke_width = 5 if slug == "total" else 3
        parts.extend(
            (
                f'<line x1="{x}" y1="93" x2="{x + 42}" y2="93" '
                f'stroke="{color}" stroke-width="{stroke_width}"'
                f'{dash_attribute}/>',
                f'<text class="legend" x="{x + 52}" y="100">'
                f"{html.escape(label)}</text>",
            )
        )
    parts.extend(
        (
            f'<text class="note" x="{width - right}" y="{top - 25}" '
            f'text-anchor="end">○ 民國 130 年暫估：各地補 2 月</text>',
            "</svg>",
        )
    )
    return "\n".join(parts) + "\n"


def write_chart(csv_path: Path, output_path: Path) -> Path:
    rows = load_chart_rows(csv_path)
    svg = render_svg(rows)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=output_path.parent,
        prefix=f".{output_path.name}.",
        suffix=".tmp",
        delete=False,
    ) as stream:
        stream.write(svg)
        temporary = Path(stream.name)
    try:
        temporary.replace(output_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return output_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="產生竹竹苗會考人口推估 SVG")
    parser.add_argument(
        "--data-root",
        type=Path,
        default=REPOSITORY_ROOT / "data" / "population",
    )
    parser.add_argument("--input", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=REPOSITORY_ROOT
        / "docs"
        / "images"
        / SVG_FILENAME,
    )
    arguments = parser.parse_args(argv)
    try:
        csv_path = arguments.input or find_latest_exam_csv(arguments.data_root)
        destination = write_chart(csv_path, arguments.output)
    except ChartDataError as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 1
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
