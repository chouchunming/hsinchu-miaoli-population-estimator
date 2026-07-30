from pathlib import Path
import json
import tempfile
import unittest

from exam_population.analysis import (
    annual_registered_migration_rows,
    exam_population_rows,
    grade_cohort_change_rows,
)
from exam_population.models import MigrationSnapshot, PopulationSnapshot
from exam_population.report import write_exports


REGIONS = ("新竹縣", "新竹市", "苗栗縣")


def age_values(scale=1):
    return {age: (1200 + age * 24) * scale for age in range(0, 20)}


class FakeRepository:
    def __init__(self, population_by_region, migration_by_region=None):
        self.population_by_region = population_by_region
        self.migration_by_region = migration_by_region or {}

    def population_series(self, region):
        return list(self.population_by_region.get(region, ()))

    def migration_series(self, region):
        return list(self.migration_by_region.get(region, ()))

    def adopted_artifacts(self):
        return [
            {
                "id": 1,
                "dataset": "age_population",
                "region": "新竹縣",
                "roc_year": 115,
                "month": 6,
                "sha256": "a" * 64,
                "archive_path": "raw/example.xlsx",
            }
        ]


def current_repo():
    return FakeRepository(
        {
            region: [PopulationSnapshot(None, region, 115, 6, age_values(index))]
            for index, region in enumerate(REGIONS, 1)
        }
    )


class ExamAnalysisTests(unittest.TestCase):
    def test_exam_rows_have_16_years_and_three_region_total(self):
        rows = exam_population_rows(current_repo(), 115, 130)
        self.assertEqual(len(rows), 16)
        self.assertEqual(rows[0]["會考年度"], 115)
        self.assertEqual(rows[0]["出生區間"], "99-09～100-08")
        self.assertEqual(
            rows[0]["三區合計"],
            sum(
                rows[0][field]
                for field in (
                    "新竹縣預估人數",
                    "新竹市預估人數",
                    "苗栗縣預估人數",
                )
            ),
        )
        self.assertIn("非實際報考人數", rows[0]["估算方法"])
        self.assertEqual(rows[0]["資料完整性"], "完整 cohort")
        self.assertEqual(
            rows[-1]["資料完整性"],
            "暫估：新竹縣補2月、新竹市補2月、苗栗縣補2月",
        )


class RegisteredMigrationAnalysisTests(unittest.TestCase):
    def test_registered_totals_are_named_and_partial_year_is_explicit(self):
        migrations = {
            region: [
                MigrationSnapshot(None, region, 115, month, 100 * index, 80 * index)
                for month in range(1, 7)
            ]
            for index, region in enumerate(REGIONS, 1)
        }
        rows = annual_registered_migration_rows(
            FakeRepository({}, migrations)
        )
        county = next(
            row for row in rows if row["年度"] == 115 and row["地區"] == "新竹縣"
        )
        self.assertEqual(county["戶籍遷入登記總數"], 600)
        self.assertEqual(county["戶籍登記淨變化"], 120)
        self.assertEqual(county["涵蓋月份"], "01–06")
        self.assertEqual(county["資料完整性"], "部分年度")
        self.assertIn("包含縣市內跨區移動", county["統計口徑"])

    def test_complete_year_is_distinct(self):
        migrations = {
            region: [
                MigrationSnapshot(None, region, 114, month, 10, 8)
                for month in range(1, 13)
            ]
            for region in REGIONS
        }
        rows = annual_registered_migration_rows(FakeRepository({}, migrations))
        county = next(row for row in rows if row["地區"] == "新竹縣")
        self.assertEqual(county["資料完整性"], "完整年度")


class GradeAnalysisTests(unittest.TestCase):
    def test_june_pair_maps_to_previous_school_year(self):
        populations = {
            region: [
                PopulationSnapshot(None, region, 114, 6, age_values(index)),
                PopulationSnapshot(
                    None,
                    region,
                    115,
                    6,
                    {age: value + 120 for age, value in age_values(index).items()},
                ),
            ]
            for index, region in enumerate(REGIONS, 1)
        }
        rows = grade_cohort_change_rows(FakeRepository(populations))
        row = next(
            item
            for item in rows
            if item["地區"] == "新竹市" and item["班別或年級"] == "幼幼班"
        )
        self.assertEqual(row["學年度"], 114)
        self.assertEqual(row["比較期間"], "114-06～115-06")
        self.assertEqual(row["資料完整性"], "年中暫估")
        self.assertNotEqual(row["人口增減"], "")

    def test_missing_baseline_is_not_zero(self):
        populations = {
            region: [PopulationSnapshot(None, region, 115, 6, age_values())]
            for region in REGIONS
        }
        rows = grade_cohort_change_rows(FakeRepository(populations))
        row = next(
            item
            for item in rows
            if item["地區"] == "苗栗縣" and item["班別或年級"] == "國中二年級"
        )
        self.assertEqual(row["期初人口"], "")
        self.assertEqual(row["人口增減"], "")
        self.assertEqual(row["資料完整性"], "基期不足")


class ReportTests(unittest.TestCase):
    def test_exports_use_utf8_bom_and_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = exam_population_rows(current_repo(), 116, 130)
            export_dir = write_exports(
                root,
                exam_rows=rows,
                migration_rows=[],
                grade_rows=[],
                artifacts=current_repo().adopted_artifacts(),
                warnings=("historical gap",),
                status="partial",
                backfill_from=(114, 1),
            )
            exam_path = export_dir / "exam_population_116_130.csv"
            self.assertTrue(exam_path.read_bytes().startswith(b"\xef\xbb\xbf"))
            manifest = json.loads((export_dir / "run_manifest.json").read_text())
            self.assertEqual(manifest["status"], "partial")
            self.assertEqual(manifest["backfill_from"], "114-01")


if __name__ == "__main__":
    unittest.main()
