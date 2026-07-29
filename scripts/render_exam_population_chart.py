#!/usr/bin/env python3
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


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
    candidates = sorted(
        Path(data_root).glob("exports/*/exam_population_116_130.csv")
    )
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

    if tuple(row.exam_year for row in rows) != tuple(range(116, 131)):
        raise ChartDataError("會考年度必須連續且恰為 116–130")
    if any(row.provisional for row in rows[:-1]) or not rows[-1].provisional:
        raise ChartDataError("只有民國 130 年必須標示為暫估")
    return tuple(rows)
