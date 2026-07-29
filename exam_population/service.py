from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError

from .analysis import (
    annual_registered_migration_rows,
    exam_population_rows,
    grade_cohort_change_rows,
)
from .archive import Archive
from .models import (
    ArtifactMetadata,
    GapReport,
    REGIONS,
    RunResult,
    SnapshotValidationError,
)
from .parsers import (
    parse_age_csv,
    parse_age_xlsx,
    parse_migration_csv,
    parse_migration_xlsx,
)
from .report import write_exports
from .repository import PopulationRepository
from .sources import (
    DatasetCandidate,
    HsinchuCitySource,
    HsinchuCountySource,
    HttpClient,
    MiaoliCountySource,
    SourceDiscoveryError,
    current_candidate,
)


DATASETS = ("age_population", "migration")


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_data_root() -> Path:
    return repository_root() / "data" / "population"


def current_roc_month() -> tuple[int, int]:
    now = datetime.now()
    return now.year - 1911, now.month


def default_sources(http=None):
    http = http or HttpClient()
    return (
        HsinchuCountySource(http),
        HsinchuCitySource(http),
        MiaoliCountySource(http),
    )


def _metadata(candidate: DatasetCandidate) -> ArtifactMetadata:
    return ArtifactMetadata(
        dataset=candidate.dataset,
        region=candidate.region,
        roc_year=candidate.roc_year,
        month=candidate.month,
        source_page_url=candidate.source_page_url,
        download_url=candidate.download_url,
        fetched_at=datetime.now(UTC).isoformat(timespec="microseconds"),
        original_filename=candidate.original_filename,
        media_type=candidate.media_type,
    )


def _parse(candidate: DatasetCandidate, data: bytes):
    parser = {
        ("age_population", ".csv"): parse_age_csv,
        ("age_population", ".xlsx"): parse_age_xlsx,
        ("migration", ".csv"): parse_migration_csv,
        ("migration", ".xlsx"): parse_migration_xlsx,
    }.get((candidate.dataset, candidate.extension))
    if parser is None:
        raise ValueError(
            f"不支援的資料格式：{candidate.dataset} {candidate.extension}"
        )
    return parser(data, candidate.region)


def _candidate_key(candidate: DatasetCandidate) -> tuple[str, str]:
    return candidate.region, candidate.dataset


def _gap_warnings(
    repo: PopulationRepository,
    start: tuple[int, int],
    end_by_key: dict[tuple[str, str], tuple[int, int]],
) -> tuple[str, ...]:
    warnings = []
    for region in REGIONS:
        for dataset in DATASETS:
            end = end_by_key.get((region, dataset))
            if end is None or end < start:
                continue
            report = repo.gaps(region, dataset, start, end)
            if report.missing:
                months = "、".join(
                    f"{item.roc_year}-{item.month:02d}" for item in report.missing
                )
                warnings.append(f"{region} {dataset} 缺少：{months}")
            if report.unsupported_media:
                months = "、".join(
                    f"{item.roc_year}-{item.month:02d}"
                    for item in report.unsupported_media
                )
                warnings.append(
                    f"{region} {dataset} unsupported_media：{months}"
                )
    return tuple(warnings)


def update(
    data_root: Path | str | None = None,
    *,
    sources=None,
    start_year: int = 116,
    end_year: int = 130,
    backfill_from: tuple[int, int] = (114, 1),
    range_end: tuple[int, int] | None = None,
) -> RunResult:
    data_root = Path(data_root or default_data_root()).resolve()
    range_end = range_end or current_roc_month()
    archive = Archive(data_root)
    errors: list[str] = []
    discovery_warnings: list[str] = []
    inventories: dict[str, tuple[DatasetCandidate, ...]] = {}
    source_list = tuple(sources or default_sources())

    with PopulationRepository(data_root / "population.sqlite3") as repo:
        for source in source_list:
            region = getattr(source, "REGION", getattr(source, "region", "unknown"))
            try:
                inventories[region] = tuple(
                    source.discover_available(backfill_from, range_end)
                )
            except (
                HTTPError,
                URLError,
                TimeoutError,
                OSError,
                SourceDiscoveryError,
            ) as exc:
                inventories[region] = ()
                errors.append(f"{region} discovery 失敗：{exc}")

        current: dict[tuple[str, str], DatasetCandidate] = {}
        for region in REGIONS:
            inventory = inventories.get(region, ())
            for dataset in DATASETS:
                try:
                    current[(region, dataset)] = current_candidate(
                        inventory, dataset
                    )
                except Exception as exc:
                    errors.append(f"{region} {dataset} current candidate 失敗：{exc}")

        current_urls = {item.download_url for item in current.values()}
        failed_current_urls: set[str] = set()
        for source in source_list:
            region = getattr(source, "REGION", getattr(source, "region", "unknown"))
            for candidate in inventories.get(region, ()):
                is_current = candidate.download_url in current_urls
                if (
                    not is_current
                    and repo.has_terminal_download(candidate.download_url)
                ):
                    continue
                metadata = _metadata(candidate)
                try:
                    data = source.http.get(candidate.download_url)
                except (HTTPError, URLError, TimeoutError, OSError) as exc:
                    repo.record_fetch_failure(metadata, str(exc))
                    message = (
                        f"{candidate.region} {candidate.dataset} "
                        f"{candidate.roc_year}-{candidate.month:02d} 下載失敗：{exc}"
                    )
                    if is_current:
                        errors.append(message)
                        failed_current_urls.add(candidate.download_url)
                    else:
                        discovery_warnings.append(message)
                    continue
                stored = archive.store(metadata, data)
                if not candidate.supported_for_parse:
                    repo.record_unsupported(stored)
                    continue
                try:
                    snapshot = _parse(candidate, data)
                except SnapshotValidationError as exc:
                    repo.record_failure(stored, str(exc))
                    message = (
                        f"{candidate.region} {candidate.dataset} "
                        f"{candidate.roc_year}-{candidate.month:02d} 解析失敗：{exc}"
                    )
                    if is_current:
                        errors.append(message)
                        failed_current_urls.add(candidate.download_url)
                    else:
                        discovery_warnings.append(message)
                    continue
                repo.ingest(stored, snapshot)

        for key, candidate in current.items():
            if candidate.download_url in failed_current_urls:
                continue
            if candidate.dataset == "age_population":
                adopted = repo.latest_population(
                    candidate.region, candidate.roc_year, candidate.month
                )
            else:
                adopted = repo.latest_migration(
                    candidate.region, candidate.roc_year, candidate.month
                )
            if adopted is None:
                errors.append(
                    f"{candidate.region} {candidate.dataset} current candidate 未採用"
                )

        if errors:
            return RunResult(None, tuple(errors + discovery_warnings), "failed")

        end_by_key = {
            _candidate_key(candidate): max(
                (
                    (item.roc_year, item.month)
                    for item in inventories[candidate.region]
                    if item.dataset == candidate.dataset
                ),
                default=(candidate.roc_year, candidate.month),
            )
            for candidate in current.values()
        }
        warnings = tuple(discovery_warnings) + _gap_warnings(
            repo, backfill_from, end_by_key
        )
        status = "partial" if warnings else "success"
        export_dir = write_exports(
            data_root,
            exam_rows=exam_population_rows(repo, start_year, end_year),
            migration_rows=annual_registered_migration_rows(repo),
            grade_rows=grade_cohort_change_rows(repo),
            artifacts=repo.adopted_artifacts(),
            warnings=warnings,
            status=status,
            backfill_from=backfill_from,
        )
        return RunResult(export_dir, warnings, status)


def check_gaps(
    data_root: Path | str | None = None,
    *,
    backfill_from: tuple[int, int] = (114, 1),
) -> GapReport:
    data_root = Path(data_root or default_data_root()).resolve()
    missing = []
    unsupported = []
    with PopulationRepository(data_root / "population.sqlite3") as repo:
        for region in REGIONS:
            for dataset in DATASETS:
                end = repo.latest_observed_month(region, dataset)
                if end is None or end < backfill_from:
                    continue
                report = repo.gaps(region, dataset, backfill_from, end)
                missing.extend(report.missing)
                unsupported.extend(report.unsupported_media)
    return GapReport(tuple(sorted(missing)), tuple(sorted(unsupported)))


def analyze(
    data_root: Path | str | None = None,
    *,
    start_year: int = 116,
    end_year: int = 130,
    backfill_from: tuple[int, int] = (114, 1),
) -> RunResult:
    data_root = Path(data_root or default_data_root()).resolve()
    gap_report = check_gaps(data_root, backfill_from=backfill_from)
    warnings = tuple(
        [
            *(
                f"{item.region} {item.dataset} 缺少 "
                f"{item.roc_year}-{item.month:02d}"
                for item in gap_report.missing
            ),
            *(
                f"{item.region} {item.dataset} unsupported_media "
                f"{item.roc_year}-{item.month:02d}"
                for item in gap_report.unsupported_media
            ),
        ]
    )
    with PopulationRepository(data_root / "population.sqlite3") as repo:
        status = "partial" if warnings else "success"
        export_dir = write_exports(
            data_root,
            exam_rows=exam_population_rows(repo, start_year, end_year),
            migration_rows=annual_registered_migration_rows(repo),
            grade_rows=grade_cohort_change_rows(repo),
            artifacts=repo.adopted_artifacts(),
            warnings=warnings,
            status=status,
            backfill_from=backfill_from,
        )
    return RunResult(export_dir, warnings, status)
