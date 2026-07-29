from __future__ import annotations

import csv
from datetime import UTC, datetime
import json
from pathlib import Path
import tempfile


EXAM_FIELDS = (
    "會考年度",
    "西元年度",
    "出生區間",
    "新竹縣預估人數",
    "新竹縣資料年月",
    "新竹市預估人數",
    "新竹市資料年月",
    "苗栗縣預估人數",
    "苗栗縣資料年月",
    "三區合計",
    "資料完整性",
    "估算方法",
)
MIGRATION_FIELDS = (
    "年度",
    "地區",
    "戶籍遷入登記總數",
    "戶籍遷出登記總數",
    "戶籍登記淨變化",
    "涵蓋月份",
    "資料完整性",
    "統計口徑",
)
GRADE_FIELDS = (
    "比較期間",
    "學年度",
    "地區",
    "班別或年級",
    "出生區間",
    "期初人口",
    "期末人口",
    "人口增減",
    "增減率",
    "資料完整性",
)


def _write_csv(path: Path, fields: tuple[str, ...], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_exports(
    data_root: Path,
    *,
    exam_rows: list[dict],
    migration_rows: list[dict],
    grade_rows: list[dict],
    artifacts: list[dict],
    warnings: tuple[str, ...],
    status: str,
    backfill_from: tuple[int, int],
) -> Path:
    data_root = Path(data_root).resolve()
    exports = data_root / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(UTC)
    name = generated_at.strftime("%Y%m%dT%H%M%S.%fZ")
    destination = exports / name
    temporary = Path(tempfile.mkdtemp(prefix=f".{name}.", dir=exports))
    try:
        start_year = exam_rows[0]["會考年度"] if exam_rows else 116
        end_year = exam_rows[-1]["會考年度"] if exam_rows else 130
        _write_csv(
            temporary / f"exam_population_{start_year}_{end_year}.csv",
            EXAM_FIELDS,
            exam_rows,
        )
        _write_csv(
            temporary / "annual_registered_migration.csv",
            MIGRATION_FIELDS,
            migration_rows,
        )
        _write_csv(
            temporary / "annual_grade_cohort_change.csv",
            GRADE_FIELDS,
            grade_rows,
        )
        as_of = {}
        for artifact in artifacts:
            key = f"{artifact['region']}:{artifact['dataset']}"
            value = f"{int(artifact['roc_year'])}-{int(artifact['month']):02d}"
            as_of[key] = max(as_of.get(key, value), value)
        manifest = {
            "generated_at": generated_at.isoformat(),
            "schema_version": 1,
            "status": status,
            "backfill_from": f"{backfill_from[0]}-{backfill_from[1]:02d}",
            "row_counts": {
                "exam_population": len(exam_rows),
                "annual_registered_migration": len(migration_rows),
                "annual_grade_cohort_change": len(grade_rows),
            },
            "as_of": as_of,
            "warnings": list(warnings),
            "artifacts": artifacts,
        }
        (temporary / "run_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)
        return destination
    except Exception:
        for path in temporary.iterdir():
            path.unlink()
        temporary.rmdir()
        raise
