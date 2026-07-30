#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import sqlite3


GRADE_LABELS = {
    "幼幼班",
    "幼兒園小班",
    "幼兒園中班",
    "幼兒園大班",
    "國小一年級",
    "國小二年級",
    "國小三年級",
    "國小四年級",
    "國小五年級",
    "國小六年級",
    "國中一年級",
    "國中二年級",
}


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def validate(data_root: Path | str) -> list[str]:
    data_root = Path(data_root).resolve()
    errors: list[str] = []

    for path in (data_root / "raw").rglob("*"):
        if not path.is_file():
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if path.stem != actual:
            errors.append(f"archive SHA-256 不符：{path}")

    database = data_root / "population.sqlite3"
    if not database.exists():
        return errors + [f"SQLite 不存在：{database}"]
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        artifacts = {
            int(row["id"]): dict(row)
            for row in connection.execute(
                "SELECT id, sha256, archive_path FROM artifacts"
            )
        }
        for artifact in artifacts.values():
            path = Path(str(artifact["archive_path"]))
            if not path.is_absolute():
                path = data_root / path
            if not path.exists():
                errors.append(f"artifact 檔案不存在：{path}")
                continue
            if hashlib.sha256(path.read_bytes()).hexdigest() != artifact["sha256"]:
                errors.append(f"SQLite artifact SHA-256 不符：{path}")
    finally:
        connection.close()

    export_dirs = sorted(
        path
        for path in (data_root / "exports").glob("*")
        if path.is_dir() and not path.name.startswith(".")
    )
    if not export_dirs:
        return errors + ["找不到 exports"]
    export_dir = export_dirs[-1]
    manifest_path = export_dir / "run_manifest.json"
    if not manifest_path.exists():
        return errors + [f"manifest 不存在：{manifest_path}"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    exam_files = list(export_dir.glob("exam_population_*.csv"))
    if len(exam_files) != 1:
        errors.append("會考 CSV 數量應為 1")
    else:
        exam_rows = _rows(exam_files[0])
        years = [int(row["會考年度"]) for row in exam_rows]
        if years != list(range(115, 131)):
            errors.append(f"會考年度不是 115–130：{years}")
        for row in exam_rows:
            regional = sum(
                int(row[field])
                for field in (
                    "新竹縣預估人數",
                    "新竹市預估人數",
                    "苗栗縣預估人數",
                )
            )
            if int(row["三區合計"]) != regional:
                errors.append(f"會考三區合計錯誤：{row['會考年度']}")

    migration_path = export_dir / "annual_registered_migration.csv"
    if migration_path.exists():
        for row in _rows(migration_path):
            expected = (
                int(row["戶籍遷入登記總數"])
                - int(row["戶籍遷出登記總數"])
            )
            if int(row["戶籍登記淨變化"]) != expected:
                errors.append(
                    f"戶籍登記淨變化錯誤：{row['年度']} {row['地區']}"
                )
            if "包含縣市內跨區移動" not in row["統計口徑"]:
                errors.append(f"戶籍動態缺少口徑聲明：{row['地區']}")

    grade_path = export_dir / "annual_grade_cohort_change.csv"
    if grade_path.exists():
        grade_rows = _rows(grade_path)
        labels = {row["班別或年級"] for row in grade_rows}
        if labels != GRADE_LABELS:
            errors.append(f"年級 labels 不符：{sorted(labels)}")
        for row in grade_rows:
            endpoint = row["比較期間"].split("～")[-1]
            year_text, month_text = endpoint.split("-")
            if month_text == "06" and int(row["學年度"]) != int(year_text) - 1:
                errors.append(f"六月學年度映射錯誤：{row['比較期間']}")

    for item in manifest.get("artifacts", []):
        artifact_id = int(item["id"])
        database_item = artifacts.get(artifact_id)
        if database_item is None:
            errors.append(f"manifest artifact 不在 SQLite：{artifact_id}")
            continue
        if item["sha256"] != database_item["sha256"]:
            errors.append(f"manifest artifact hash 不符：{artifact_id}")
        if item["archive_path"] != database_item["archive_path"]:
            errors.append(f"manifest artifact path 不符：{artifact_id}")
    return errors


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    arguments = parser.parse_args(argv)
    errors = validate(arguments.data_root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("live artifact validation: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
