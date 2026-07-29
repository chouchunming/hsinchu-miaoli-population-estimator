from datetime import UTC, datetime
from pathlib import Path
import sqlite3
import tempfile
import unittest

from exam_population.archive import Archive
from exam_population.models import (
    ArtifactMetadata,
    GapItem,
    MigrationSnapshot,
    PopulationSnapshot,
)
from exam_population.repository import PopulationRepository


def metadata(
    *,
    dataset="age_population",
    region="新竹縣",
    year=115,
    month=6,
    fetched_at="2026-07-29T12:00:00.000001+00:00",
    filename="official.xlsx",
    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
):
    return ArtifactMetadata(
        dataset=dataset,
        region=region,
        roc_year=year,
        month=month,
        source_page_url="https://example.test/index",
        download_url=f"https://example.test/{filename}",
        fetched_at=fetched_at,
        original_filename=filename,
        media_type=media_type,
    )


class StorageTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.archive = Archive(self.root)
        self.repo = PopulationRepository(self.root / "population.sqlite3")

    def tearDown(self):
        self.repo.close()
        self.temporary.cleanup()


class ArchiveTests(StorageTestCase):
    def test_same_scope_and_bytes_reuse_content_addressed_path(self):
        first = self.archive.store(metadata(), b"official bytes")
        second = self.archive.store(
            metadata(fetched_at="2026-07-29T12:01:00.000001+00:00"),
            b"official bytes",
        )
        self.assertEqual(first.path, second.path)
        self.assertEqual(len(list((self.root / "raw").rglob("*.xlsx"))), 1)

    def test_same_month_changed_bytes_preserve_versions(self):
        first = self.archive.store(metadata(), b"version one")
        second = self.archive.store(metadata(), b"version two")
        self.assertNotEqual(first.path, second.path)
        self.assertTrue(first.path.exists())
        self.assertTrue(second.path.exists())


class RepositoryTests(StorageTestCase):
    def stored(self, data=b"population"):
        return self.archive.store(metadata(), data)

    def population(self, artifact_id=None):
        return PopulationSnapshot(artifact_id, "新竹縣", 115, 6, {0: 100, 1: 90})

    def test_ingest_is_idempotent_and_records_fetch_event(self):
        stored = self.stored()
        first = self.repo.ingest(stored, self.population())
        second = self.repo.ingest(stored, self.population())
        self.assertEqual(first, second)
        self.assertEqual(self.repo.count_artifacts(), 1)
        self.assertEqual(self.repo.count_age_rows(), 2)
        self.assertEqual(self.repo.count_fetch_events(), 1)

    def test_latest_success_ignores_newer_failure(self):
        good = self.stored()
        good_id = self.repo.ingest(good, self.population())
        bad = self.archive.store(
            metadata(fetched_at="2026-07-29T13:00:00.000001+00:00"),
            b"bad",
        )
        self.repo.record_failure(bad, "header missing")
        self.assertEqual(
            self.repo.latest_population("新竹縣", 115, 6).artifact_id,
            good_id,
        )

    def test_stored_artifact_for_download_reconstructs_local_archive(self):
        stored = self.stored()
        self.repo.record_failure(stored, "header missing")
        recovered = self.repo.stored_artifact_for_download(
            stored.metadata.download_url
        )
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.path, stored.path)
        self.assertEqual(recovered.sha256, stored.sha256)
        self.assertEqual(recovered.metadata, stored.metadata)

    def test_transaction_rolls_back_artifact_and_rows(self):
        self.repo.connection.execute(
            """
            CREATE TRIGGER fail_second_age
            BEFORE INSERT ON age_population_monthly
            WHEN NEW.age = 1
            BEGIN
              SELECT RAISE(ABORT, 'forced rollback');
            END
            """
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.ingest(self.stored(), self.population())
        self.assertEqual(self.repo.count_artifacts(), 0)
        self.assertEqual(self.repo.count_age_rows(), 0)

    def test_migration_round_trip(self):
        stored = self.archive.store(
            metadata(dataset="migration", filename="migration.xlsx"),
            b"migration",
        )
        artifact_id = self.repo.ingest(
            stored,
            MigrationSnapshot(None, "新竹縣", 115, 6, 1880, 1660),
        )
        snapshot = self.repo.latest_migration("新竹縣", 115, 6)
        self.assertEqual(snapshot.artifact_id, artifact_id)
        self.assertEqual(snapshot.registered_moved_in_total, 1880)

    def test_unsupported_pdf_is_separate_from_missing_gap(self):
        stored = self.archive.store(
            metadata(
                dataset="migration",
                region="新竹市",
                year=114,
                month=2,
                filename="11402.pdf",
                media_type="application/pdf",
            ),
            b"%PDF",
        )
        self.repo.record_unsupported(stored)
        report = self.repo.gaps("新竹市", "migration", (114, 1), (114, 3))
        self.assertEqual(
            report.unsupported_media,
            (GapItem("新竹市", "migration", 114, 2),),
        )
        self.assertEqual(
            report.missing,
            (
                GapItem("新竹市", "migration", 114, 1),
                GapItem("新竹市", "migration", 114, 3),
            ),
        )

    def test_fetch_failure_is_durable_without_fake_artifact(self):
        failure_id = self.repo.record_fetch_failure(
            metadata(),
            "timeout",
        )
        self.assertGreater(failure_id, 0)
        self.assertEqual(self.repo.count_fetch_failures(), 1)
        self.assertEqual(self.repo.count_artifacts(), 0)


if __name__ == "__main__":
    unittest.main()
