from __future__ import annotations

import re
from typing import Iterable, Sequence

from .models import MigrationSnapshot, PopulationSnapshot, SnapshotValidationError
from .tabular import decode_csv_rows, read_xlsx_rows


DATE_PATTERN = re.compile(r"中華民國\s*(\d+)\s*年\s*(\d+)\s*月(?:底)?")
AGE_PATTERN = re.compile(r"(?:_|^)(\d+)歲$")
REGISTERED_MOVED_IN_TOTAL_HEADERS = (
    "遷入人數_合計",
    "遷入人數",
    "遷入人口數",
)
REGISTERED_MOVED_OUT_TOTAL_HEADERS = (
    "遷出人數_合計",
    "遷出人數",
    "遷出人口數",
)
REGION_HEADERS = ("區域別", "區域", "縣市別")
SEX_HEADERS = ("性別",)


def _normalize(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).replace("\u3000", "")


def _reference_month(rows: Sequence[Sequence[object]]) -> tuple[int, int]:
    for row in rows:
        for value in row:
            match = DATE_PATTERN.search(_normalize(value))
            if match:
                year, month = map(int, match.groups())
                if 1 <= month <= 12:
                    return year, month
    raise SnapshotValidationError("找不到有效的中華民國統計年月")


def _header_index(headers: Sequence[object], accepted: Iterable[str]) -> int | None:
    normalized = [_normalize(value) for value in headers]
    for label in accepted:
        if label in normalized:
            return normalized.index(label)
    return None


def _integer(value: object, label: str) -> int:
    text = _normalize(value).replace(",", "")
    if not text:
        raise SnapshotValidationError(f"{label}不可空白")
    try:
        number = int(text)
    except ValueError as exc:
        raise SnapshotValidationError(f"{label}不是整數：{value}") from exc
    if number < 0:
        raise SnapshotValidationError(f"{label}必須是非負整數")
    return number


def _total_row(
    rows: Sequence[Sequence[object]],
    header_position: int,
    region_column: int,
    sex_column: int | None,
    region: str,
) -> Sequence[object]:
    matches = []
    for row in rows[header_position + 1 :]:
        region_value = _normalize(row[region_column] if region_column < len(row) else "")
        sex_value = _normalize(row[sex_column] if sex_column is not None and sex_column < len(row) else "計")
        is_total_region = region_value in {"總計", "合計", region} or "總計" in region_value
        if is_total_region and sex_value in {"計", "合計", "總計"}:
            matches.append(row)
    if len(matches) != 1:
        raise SnapshotValidationError(
            f"{region} 總計列數量應為 1，實際為 {len(matches)}"
        )
    return matches[0]


def parse_age_rows(
    rows: Sequence[Sequence[object]],
    region: str,
) -> PopulationSnapshot:
    year, month = _reference_month(rows)
    header_position = -1
    age_columns: dict[int, int] = {}
    region_column: int | None = None
    sex_column: int | None = None
    for position, row in enumerate(rows):
        candidate: dict[int, int] = {}
        duplicate: int | None = None
        for index, value in enumerate(row):
            label = _normalize(value)
            if label.endswith("100歲以上"):
                continue
            match = AGE_PATTERN.search(label)
            if not match:
                continue
            age = int(match.group(1))
            if age in candidate:
                duplicate = age
                break
            candidate[age] = index
        if duplicate is not None:
            raise SnapshotValidationError(f"重複年齡欄：{duplicate}")
        candidate_region = _header_index(row, REGION_HEADERS)
        candidate_sex = _header_index(row, SEX_HEADERS)
        if candidate and candidate_region is None and position > 0:
            previous_row = rows[position - 1]
            candidate_region = _header_index(previous_row, REGION_HEADERS)
            candidate_sex = _header_index(previous_row, SEX_HEADERS)
        if candidate and candidate_region is not None:
            header_position = position
            age_columns = candidate
            region_column = candidate_region
            sex_column = candidate_sex
            break
    if header_position < 0 or region_column is None:
        raise SnapshotValidationError("找不到單歲人口表頭")
    total = _total_row(rows, header_position, region_column, sex_column, region)
    values = {
        age: _integer(total[index] if index < len(total) else "", f"{age} 歲人口")
        for age, index in age_columns.items()
    }
    return PopulationSnapshot(None, region, year, month, values)


def parse_migration_rows(
    rows: Sequence[Sequence[object]],
    region: str,
) -> MigrationSnapshot:
    year, month = _reference_month(rows)
    for position, row in enumerate(rows):
        region_column = _header_index(row, REGION_HEADERS)
        moved_in_column = _header_index(row, REGISTERED_MOVED_IN_TOTAL_HEADERS)
        moved_out_column = _header_index(row, REGISTERED_MOVED_OUT_TOTAL_HEADERS)
        if region_column is None or moved_in_column is None or moved_out_column is None:
            continue
        sex_column = _header_index(row, SEX_HEADERS)
        total = _total_row(rows, position, region_column, sex_column, region)
        return MigrationSnapshot(
            artifact_id=None,
            region=region,
            roc_year=year,
            month=month,
            registered_moved_in_total=_integer(
                total[moved_in_column] if moved_in_column < len(total) else "",
                "戶籍遷入登記總數",
            ),
            registered_moved_out_total=_integer(
                total[moved_out_column] if moved_out_column < len(total) else "",
                "戶籍遷出登記總數",
            ),
        )
    raise SnapshotValidationError("找不到戶籍遷入／遷出登記總數欄位")


def parse_age_csv(data: bytes, region: str) -> PopulationSnapshot:
    return parse_age_rows(decode_csv_rows(data), region)


def parse_age_xlsx(data: bytes, region: str) -> PopulationSnapshot:
    return parse_age_rows(read_xlsx_rows(data), region)


def parse_migration_csv(data: bytes, region: str) -> MigrationSnapshot:
    return parse_migration_rows(decode_csv_rows(data), region)


def parse_migration_xlsx(data: bytes, region: str) -> MigrationSnapshot:
    return parse_migration_rows(read_xlsx_rows(data), region)
