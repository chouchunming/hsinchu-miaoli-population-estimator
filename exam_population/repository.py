from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
import sqlite3
from typing import Literal

from .models import (
    ArtifactMetadata,
    GapItem,
    GapReport,
    MigrationSnapshot,
    PopulationSnapshot,
    StoredArtifact,
)


SCHEMA_VERSION = 1


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _month_range(
    start: tuple[int, int],
    end: tuple[int, int],
) -> Iterable[tuple[int, int]]:
    current = start
    while current <= end:
        yield current
        current = _next_month(*current)


class PopulationRepository:
    def __init__(self, database_path: Path):
        self.database_path = Path(database_path).resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.database_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA busy_timeout = 5000")
        self._initialize()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "PopulationRepository":
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def _initialize(self) -> None:
        with self.connection:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_version(
                  version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS artifacts(
                  id INTEGER PRIMARY KEY,
                  dataset TEXT NOT NULL,
                  region TEXT NOT NULL,
                  roc_year INTEGER NOT NULL,
                  month INTEGER NOT NULL CHECK(month BETWEEN 1 AND 12),
                  sha256 TEXT NOT NULL,
                  archive_path TEXT NOT NULL,
                  original_filename TEXT NOT NULL,
                  media_type TEXT NOT NULL,
                  source_page_url TEXT NOT NULL,
                  download_url TEXT NOT NULL,
                  first_fetched_at TEXT NOT NULL,
                  parse_status TEXT NOT NULL CHECK(
                    parse_status IN ('pending', 'success', 'failed', 'unsupported_media')
                  ),
                  parse_error TEXT,
                  UNIQUE(dataset, region, roc_year, month, sha256)
                );
                CREATE TABLE IF NOT EXISTS artifact_fetches(
                  artifact_id INTEGER NOT NULL REFERENCES artifacts(id),
                  fetched_at TEXT NOT NULL,
                  UNIQUE(artifact_id, fetched_at)
                );
                CREATE TABLE IF NOT EXISTS fetch_failures(
                  id INTEGER PRIMARY KEY,
                  dataset TEXT NOT NULL,
                  region TEXT NOT NULL,
                  roc_year INTEGER NOT NULL,
                  month INTEGER NOT NULL CHECK(month BETWEEN 1 AND 12),
                  source_page_url TEXT NOT NULL,
                  download_url TEXT NOT NULL,
                  attempted_at TEXT NOT NULL,
                  error TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS age_population_monthly(
                  artifact_id INTEGER NOT NULL REFERENCES artifacts(id),
                  age INTEGER NOT NULL,
                  population INTEGER NOT NULL CHECK(population >= 0),
                  PRIMARY KEY(artifact_id, age)
                );
                CREATE TABLE IF NOT EXISTS migration_monthly(
                  artifact_id INTEGER PRIMARY KEY REFERENCES artifacts(id),
                  registered_moved_in_total INTEGER NOT NULL
                    CHECK(registered_moved_in_total >= 0),
                  registered_moved_out_total INTEGER NOT NULL
                    CHECK(registered_moved_out_total >= 0)
                );
                """
            )
            row = self.connection.execute(
                "SELECT version FROM schema_version LIMIT 1"
            ).fetchone()
            if row is None:
                self.connection.execute(
                    "INSERT INTO schema_version(version) VALUES (?)",
                    (SCHEMA_VERSION,),
                )
            elif row["version"] != SCHEMA_VERSION:
                raise RuntimeError(
                    f"不支援的 SQLite schema version：{row['version']}"
                )

    def _archive_path(self, stored: StoredArtifact) -> str:
        try:
            return str(stored.path.relative_to(self.database_path.parent))
        except ValueError:
            return str(stored.path)

    def _ensure_artifact(
        self,
        stored: StoredArtifact,
        parse_status: str,
        parse_error: str | None,
    ) -> int:
        metadata = stored.metadata
        self.connection.execute(
            """
            INSERT OR IGNORE INTO artifacts(
              dataset, region, roc_year, month, sha256, archive_path,
              original_filename, media_type, source_page_url, download_url,
              first_fetched_at, parse_status, parse_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                metadata.dataset,
                metadata.region,
                metadata.roc_year,
                metadata.month,
                stored.sha256,
                self._archive_path(stored),
                metadata.original_filename,
                metadata.media_type,
                metadata.source_page_url,
                metadata.download_url,
                metadata.fetched_at,
                parse_status,
                parse_error,
            ),
        )
        row = self.connection.execute(
            """
            SELECT id, parse_status FROM artifacts
            WHERE dataset=? AND region=? AND roc_year=? AND month=? AND sha256=?
            """,
            (
                metadata.dataset,
                metadata.region,
                metadata.roc_year,
                metadata.month,
                stored.sha256,
            ),
        ).fetchone()
        artifact_id = int(row["id"])
        self.connection.execute(
            "INSERT OR IGNORE INTO artifact_fetches(artifact_id, fetched_at) VALUES (?, ?)",
            (artifact_id, metadata.fetched_at),
        )
        return artifact_id

    def ingest(
        self,
        stored: StoredArtifact,
        snapshot: PopulationSnapshot | MigrationSnapshot,
    ) -> int:
        metadata = stored.metadata
        if (metadata.region, metadata.roc_year, metadata.month) != (
            snapshot.region,
            snapshot.roc_year,
            snapshot.month,
        ):
            raise ValueError("artifact metadata 與 snapshot 年月或地區不一致")
        with self.connection:
            artifact_id = self._ensure_artifact(stored, "pending", None)
            existing = self.connection.execute(
                "SELECT parse_status FROM artifacts WHERE id=?",
                (artifact_id,),
            ).fetchone()["parse_status"]
            if existing == "success":
                return artifact_id
            if isinstance(snapshot, PopulationSnapshot):
                if metadata.dataset != "age_population":
                    raise ValueError("人口 snapshot 的 dataset 必須是 age_population")
                self.connection.executemany(
                    """
                    INSERT INTO age_population_monthly(artifact_id, age, population)
                    VALUES (?, ?, ?)
                    """,
                    (
                        (artifact_id, age, population)
                        for age, population in snapshot.age_population.items()
                    ),
                )
            else:
                if metadata.dataset != "migration":
                    raise ValueError("戶籍動態 snapshot 的 dataset 必須是 migration")
                self.connection.execute(
                    """
                    INSERT INTO migration_monthly(
                      artifact_id, registered_moved_in_total,
                      registered_moved_out_total
                    ) VALUES (?, ?, ?)
                    """,
                    (
                        artifact_id,
                        snapshot.registered_moved_in_total,
                        snapshot.registered_moved_out_total,
                    ),
                )
            self.connection.execute(
                "UPDATE artifacts SET parse_status='success', parse_error=NULL WHERE id=?",
                (artifact_id,),
            )
            return artifact_id

    def record_failure(self, stored: StoredArtifact, error: str) -> int:
        with self.connection:
            artifact_id = self._ensure_artifact(stored, "failed", error)
            self.connection.execute(
                """
                UPDATE artifacts SET parse_status='failed', parse_error=?
                WHERE id=? AND parse_status!='success'
                """,
                (error, artifact_id),
            )
            return artifact_id

    def record_unsupported(self, stored: StoredArtifact) -> int:
        with self.connection:
            artifact_id = self._ensure_artifact(
                stored,
                "unsupported_media",
                "第一版不解析此媒體格式",
            )
            self.connection.execute(
                """
                UPDATE artifacts
                SET parse_status='unsupported_media',
                    parse_error='第一版不解析此媒體格式'
                WHERE id=? AND parse_status!='success'
                """,
                (artifact_id,),
            )
            return artifact_id

    def record_fetch_failure(
        self,
        metadata: ArtifactMetadata,
        error: str,
    ) -> int:
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO fetch_failures(
                  dataset, region, roc_year, month, source_page_url,
                  download_url, attempted_at, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    metadata.dataset,
                    metadata.region,
                    metadata.roc_year,
                    metadata.month,
                    metadata.source_page_url,
                    metadata.download_url,
                    metadata.fetched_at,
                    error,
                ),
            )
            return int(cursor.lastrowid)

    def _latest_artifact_id(
        self,
        dataset: str,
        region: str,
        roc_year: int,
        month: int,
    ) -> int | None:
        row = self.connection.execute(
            """
            SELECT a.id
            FROM artifacts a
            WHERE a.dataset=? AND a.region=? AND a.roc_year=? AND a.month=?
              AND a.parse_status='success'
            ORDER BY (
              SELECT MAX(f.fetched_at)
              FROM artifact_fetches f WHERE f.artifact_id=a.id
            ) DESC, a.id DESC
            LIMIT 1
            """,
            (dataset, region, roc_year, month),
        ).fetchone()
        return None if row is None else int(row["id"])

    def latest_population(
        self,
        region: str,
        roc_year: int,
        month: int,
    ) -> PopulationSnapshot | None:
        artifact_id = self._latest_artifact_id(
            "age_population", region, roc_year, month
        )
        if artifact_id is None:
            return None
        rows = self.connection.execute(
            """
            SELECT age, population FROM age_population_monthly
            WHERE artifact_id=? ORDER BY age
            """,
            (artifact_id,),
        ).fetchall()
        return PopulationSnapshot(
            artifact_id,
            region,
            roc_year,
            month,
            {int(row["age"]): int(row["population"]) for row in rows},
        )

    def latest_migration(
        self,
        region: str,
        roc_year: int,
        month: int,
    ) -> MigrationSnapshot | None:
        artifact_id = self._latest_artifact_id("migration", region, roc_year, month)
        if artifact_id is None:
            return None
        row = self.connection.execute(
            """
            SELECT registered_moved_in_total, registered_moved_out_total
            FROM migration_monthly WHERE artifact_id=?
            """,
            (artifact_id,),
        ).fetchone()
        return MigrationSnapshot(
            artifact_id,
            region,
            roc_year,
            month,
            int(row["registered_moved_in_total"]),
            int(row["registered_moved_out_total"]),
        )

    def _successful_months(self, region: str, dataset: str) -> set[tuple[int, int]]:
        return {
            (int(row["roc_year"]), int(row["month"]))
            for row in self.connection.execute(
                """
                SELECT DISTINCT roc_year, month FROM artifacts
                WHERE region=? AND dataset=? AND parse_status='success'
                """,
                (region, dataset),
            )
        }

    def artifact_months(
        self,
        region: str,
        dataset: str,
        parse_status: str | None = None,
    ) -> set[tuple[int, int]]:
        query = "SELECT DISTINCT roc_year, month FROM artifacts WHERE region=? AND dataset=?"
        parameters: list[object] = [region, dataset]
        if parse_status is not None:
            query += " AND parse_status=?"
            parameters.append(parse_status)
        return {
            (int(row["roc_year"]), int(row["month"]))
            for row in self.connection.execute(query, parameters)
        }

    def gaps(
        self,
        region: str,
        dataset: str,
        start: tuple[int, int],
        end: tuple[int, int],
    ) -> GapReport:
        successful = self._successful_months(region, dataset)
        unsupported = self.artifact_months(region, dataset, "unsupported_media")
        missing: list[GapItem] = []
        unsupported_items: list[GapItem] = []
        for year, month in _month_range(start, end):
            item = GapItem(region, dataset, year, month)
            if (year, month) in successful:
                continue
            if (year, month) in unsupported:
                unsupported_items.append(item)
            else:
                missing.append(item)
        return GapReport(tuple(missing), tuple(unsupported_items))

    def population_series(self, region: str) -> list[PopulationSnapshot]:
        return [
            snapshot
            for year, month in sorted(self._successful_months(region, "age_population"))
            if (snapshot := self.latest_population(region, year, month)) is not None
        ]

    def migration_series(self, region: str) -> list[MigrationSnapshot]:
        return [
            snapshot
            for year, month in sorted(self._successful_months(region, "migration"))
            if (snapshot := self.latest_migration(region, year, month)) is not None
        ]

    def latest_observed_month(
        self,
        region: str,
        dataset: str,
    ) -> tuple[int, int] | None:
        row = self.connection.execute(
            """
            SELECT roc_year, month FROM artifacts
            WHERE region=? AND dataset=?
            ORDER BY roc_year DESC, month DESC LIMIT 1
            """,
            (region, dataset),
        ).fetchone()
        return None if row is None else (int(row["roc_year"]), int(row["month"]))

    def has_successful_download(self, download_url: str) -> bool:
        return (
            self.connection.execute(
                """
                SELECT 1 FROM artifacts
                WHERE download_url=? AND parse_status='success' LIMIT 1
                """,
                (download_url,),
            ).fetchone()
            is not None
        )

    def has_terminal_download(self, download_url: str) -> bool:
        return (
            self.connection.execute(
                """
                SELECT 1 FROM artifacts
                WHERE download_url=?
                  AND parse_status IN ('success', 'unsupported_media')
                LIMIT 1
                """,
                (download_url,),
            ).fetchone()
            is not None
        )

    def stored_artifact_for_download(
        self,
        download_url: str,
    ) -> StoredArtifact | None:
        row = self.connection.execute(
            """
            SELECT a.*
            FROM artifacts a
            WHERE a.download_url=?
            ORDER BY (
              SELECT MAX(f.fetched_at)
              FROM artifact_fetches f WHERE f.artifact_id=a.id
            ) DESC, a.id DESC
            LIMIT 1
            """,
            (download_url,),
        ).fetchone()
        if row is None:
            return None
        path = Path(str(row["archive_path"]))
        if not path.is_absolute():
            path = self.database_path.parent / path
        metadata = ArtifactMetadata(
            dataset=str(row["dataset"]),
            region=str(row["region"]),
            roc_year=int(row["roc_year"]),
            month=int(row["month"]),
            source_page_url=str(row["source_page_url"]),
            download_url=str(row["download_url"]),
            fetched_at=str(row["first_fetched_at"]),
            original_filename=str(row["original_filename"]),
            media_type=str(row["media_type"]),
        )
        return StoredArtifact(metadata, path, str(row["sha256"]))

    def has_download_url(self, download_url: str) -> bool:
        return (
            self.connection.execute(
                "SELECT 1 FROM artifacts WHERE download_url=? LIMIT 1",
                (download_url,),
            ).fetchone()
            is not None
        )

    def adopted_artifacts(self) -> list[dict[str, object]]:
        rows = self.connection.execute(
            """
            SELECT a.* FROM artifacts a
            WHERE a.parse_status='success'
              AND a.id=(
                SELECT a2.id FROM artifacts a2
                WHERE a2.dataset=a.dataset AND a2.region=a.region
                  AND a2.roc_year=a.roc_year AND a2.month=a.month
                  AND a2.parse_status='success'
                ORDER BY (
                  SELECT MAX(f.fetched_at) FROM artifact_fetches f
                  WHERE f.artifact_id=a2.id
                ) DESC, a2.id DESC
                LIMIT 1
              )
            ORDER BY a.dataset, a.region, a.roc_year, a.month
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def count_artifacts(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0])

    def count_age_rows(self) -> int:
        return int(
            self.connection.execute(
                "SELECT COUNT(*) FROM age_population_monthly"
            ).fetchone()[0]
        )

    def count_migration_rows(self) -> int:
        return int(
            self.connection.execute("SELECT COUNT(*) FROM migration_monthly").fetchone()[0]
        )

    def count_fetch_events(self) -> int:
        return int(
            self.connection.execute("SELECT COUNT(*) FROM artifact_fetches").fetchone()[0]
        )

    def count_fetch_failures(self) -> int:
        return int(
            self.connection.execute("SELECT COUNT(*) FROM fetch_failures").fetchone()[0]
        )
