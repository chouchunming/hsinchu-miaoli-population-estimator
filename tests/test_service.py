import csv
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from urllib.error import URLError

from exam_population.repository import PopulationRepository
from exam_population.service import (
    analyze,
    check_gaps,
    default_data_root,
    update,
)
from exam_population.cli import main
from exam_population.sources import DatasetCandidate


REGION_SLUG = {
    "新竹縣": "county",
    "新竹市": "city",
    "苗栗縣": "miaoli",
}


def csv_bytes(rows):
    output = StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


def age_data(region, year=115, month=6):
    headers = ["區域別", "性別", "總計"] + [
        f"人口數_{age}歲" for age in range(20)
    ]
    values = [region, "計", 600000] + [1200 + age * 24 for age in range(20)]
    return csv_bytes(
        [[f"中華民國{year}年{month:02d}月底"], headers, values]
    )


def migration_data(region):
    return csv_bytes(
        [
            ["中華民國115年06月"],
            ["區域別", "性別", "遷入人數_合計", "遷出人數_合計"],
            [region, "計", 1880, 1660],
        ]
    )


class FakeHttp:
    def __init__(self, data, fail_url=None):
        self.data = data
        self.fail_url = fail_url
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        if url == self.fail_url:
            raise URLError("forced outage")
        return self.data[url]


class FakeSource:
    def __init__(self, region, http):
        self.region = region
        self.http = http

    def discover_available(self, start, end):
        slug = REGION_SLUG[self.region]
        return (
            DatasetCandidate(
                "age_population",
                self.region,
                115,
                6,
                f"https://example.test/{slug}/age-index",
                f"https://example.test/{slug}/age.csv",
                "age.csv",
                ".csv",
                "text/csv",
                True,
            ),
            DatasetCandidate(
                "migration",
                self.region,
                115,
                6,
                f"https://example.test/{slug}/migration-index",
                f"https://example.test/{slug}/migration.csv",
                "migration.csv",
                ".csv",
                "text/csv",
                True,
            ),
        )


class BrokenSource:
    region = "新竹縣"

    def discover_available(self, start, end):
        raise RuntimeError("programming defect")


class HistoricalFakeSource(FakeSource):
    def discover_available(self, start, end):
        current = super().discover_available(start, end)
        slug = REGION_SLUG[self.region]
        historical = DatasetCandidate(
            "age_population",
            self.region,
            115,
            5,
            f"https://example.test/{slug}/age-index",
            f"https://example.test/{slug}/age-11505.csv",
            "age-11505.csv",
            ".csv",
            "text/csv",
            True,
        )
        return (historical, *current)


def fake_sources(fail_url=None):
    data = {}
    for region, slug in REGION_SLUG.items():
        data[f"https://example.test/{slug}/age.csv"] = age_data(region)
        data[f"https://example.test/{slug}/migration.csv"] = migration_data(region)
    http = FakeHttp(data, fail_url=fail_url)
    return [FakeSource(region, http) for region in REGION_SLUG], http


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_update_archives_six_datasets_and_publishes(self):
        sources, _http = fake_sources()
        result = update(
            self.root,
            sources=sources,
            backfill_from=(115, 6),
            range_end=(115, 6),
        )
        self.assertEqual(result.status, "success")
        self.assertTrue(result.export_dir.exists())
        self.assertEqual(len(list((self.root / "raw").rglob("*.csv"))), 6)
        with PopulationRepository(self.root / "population.sqlite3") as repo:
            self.assertEqual(repo.count_artifacts(), 6)
            self.assertEqual(repo.count_migration_rows(), 3)

    def test_second_update_reuses_artifacts_but_records_fetches(self):
        sources, _http = fake_sources()
        update(self.root, sources=sources, backfill_from=(115, 6), range_end=(115, 6))
        with PopulationRepository(self.root / "population.sqlite3") as repo:
            first = (
                repo.count_artifacts(),
                repo.count_age_rows(),
                repo.count_migration_rows(),
                repo.count_fetch_events(),
            )
        update(self.root, sources=sources, backfill_from=(115, 6), range_end=(115, 6))
        with PopulationRepository(self.root / "population.sqlite3") as repo:
            second = (
                repo.count_artifacts(),
                repo.count_age_rows(),
                repo.count_migration_rows(),
                repo.count_fetch_events(),
            )
        self.assertEqual(second[:3], first[:3])
        self.assertGreater(second[3], first[3])

    def test_second_update_retries_failed_historical_artifact(self):
        sources, http = fake_sources()
        sources[0] = HistoricalFakeSource("新竹縣", http)
        historical_url = "https://example.test/county/age-11505.csv"
        http.data[historical_url] = b"invalid csv"
        update(
            self.root,
            sources=sources,
            backfill_from=(115, 5),
            range_end=(115, 6),
        )
        with PopulationRepository(self.root / "population.sqlite3") as repo:
            self.assertIsNone(repo.latest_population("新竹縣", 115, 5))

        http.data[historical_url] = age_data("新竹縣", 115, 5)
        update(
            self.root,
            sources=sources,
            backfill_from=(115, 5),
            range_end=(115, 6),
        )
        with PopulationRepository(self.root / "population.sqlite3") as repo:
            self.assertIsNotNone(repo.latest_population("新竹縣", 115, 5))

    def test_failed_current_candidate_does_not_publish(self):
        good_sources, _http = fake_sources()
        update(
            self.root,
            sources=good_sources,
            backfill_from=(115, 6),
            range_end=(115, 6),
        )
        before = set((self.root / "exports").iterdir())
        bad_url = "https://example.test/city/migration.csv"
        bad_sources, _http = fake_sources(fail_url=bad_url)
        result = update(
            self.root,
            sources=bad_sources,
            backfill_from=(115, 6),
            range_end=(115, 6),
        )
        self.assertEqual(result.status, "failed")
        self.assertIsNone(result.export_dir)
        self.assertEqual(set((self.root / "exports").iterdir()), before)
        with PopulationRepository(self.root / "population.sqlite3") as repo:
            self.assertEqual(repo.count_fetch_failures(), 1)

    def test_analyze_is_offline_and_creates_new_export(self):
        sources, http = fake_sources()
        update(self.root, sources=sources, backfill_from=(115, 6), range_end=(115, 6))
        calls = len(http.calls)
        result = analyze(self.root, backfill_from=(115, 6))
        self.assertTrue(result.export_dir.exists())
        self.assertEqual(len(http.calls), calls)

    def test_gaps_include_month_before_earliest_artifact(self):
        sources, _http = fake_sources()
        update(self.root, sources=sources, backfill_from=(115, 6), range_end=(115, 6))
        report = check_gaps(self.root, backfill_from=(115, 5))
        self.assertTrue(
            any(item.roc_year == 115 and item.month == 5 for item in report.missing)
        )

    def test_default_data_root_is_repository_relative(self):
        self.assertEqual(
            default_data_root(),
            Path(__file__).resolve().parents[1] / "data" / "population",
        )

    def test_cli_success_and_failure_exit_codes(self):
        sources, _http = fake_sources()
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(
                    [
                        "update",
                        "--data-root",
                        str(self.root),
                        "--backfill-from",
                        "115-06",
                        "--range-end",
                        "115-06",
                    ],
                    sources=sources,
                )
        self.assertEqual(code, 0)
        bad_sources, _http = fake_sources(
            fail_url="https://example.test/city/migration.csv"
        )
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(
                [
                    "update",
                    "--data-root",
                    str(self.root),
                    "--backfill-from",
                    "115-06",
                    "--range-end",
                    "115-06",
                ],
                sources=bad_sources,
            )
        self.assertEqual(code, 1)

    def test_unexpected_discovery_error_is_not_swallowed(self):
        with self.assertRaisesRegex(RuntimeError, "programming defect"):
            update(
                self.root,
                sources=[BrokenSource()],
                backfill_from=(115, 6),
                range_end=(115, 6),
            )


if __name__ == "__main__":
    unittest.main()
