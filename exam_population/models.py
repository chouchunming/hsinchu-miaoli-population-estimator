from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping


REGIONS = ("新竹縣", "新竹市", "苗栗縣")
DATASETS = ("age_population", "migration")
PARSE_STATUSES = ("pending", "success", "failed", "unsupported_media")
RUN_STATUSES = ("success", "partial", "failed")


class SnapshotValidationError(ValueError):
    pass


def _validate_region_month(region: str, month: int) -> None:
    if region not in REGIONS:
        raise SnapshotValidationError(f"不支援的地區：{region}")
    if not 1 <= month <= 12:
        raise SnapshotValidationError("月份必須介於 1 到 12")


@dataclass(frozen=True)
class PopulationSnapshot:
    artifact_id: int | None
    region: str
    roc_year: int
    month: int
    age_population: Mapping[int, int]

    def __post_init__(self) -> None:
        _validate_region_month(self.region, self.month)
        values = dict(self.age_population)
        if any(type(age) is not int or age < 0 for age in values):
            raise SnapshotValidationError("年齡必須是非負整數")
        if any(type(value) is not int or value < 0 for value in values.values()):
            raise SnapshotValidationError("人口必須是非負整數")
        object.__setattr__(self, "age_population", MappingProxyType(values))


@dataclass(frozen=True)
class MigrationSnapshot:
    artifact_id: int | None
    region: str
    roc_year: int
    month: int
    registered_moved_in_total: int
    registered_moved_out_total: int

    def __post_init__(self) -> None:
        _validate_region_month(self.region, self.month)
        for label, value in (
            ("戶籍遷入登記總數", self.registered_moved_in_total),
            ("戶籍遷出登記總數", self.registered_moved_out_total),
        ):
            if type(value) is not int or value < 0:
                raise SnapshotValidationError(f"{label}必須是非負整數")


@dataclass(frozen=True)
class RegionEstimate:
    value: int
    imputed_future_months: int


@dataclass(frozen=True)
class ArtifactMetadata:
    dataset: str
    region: str
    roc_year: int
    month: int
    source_page_url: str
    download_url: str
    fetched_at: str
    original_filename: str
    media_type: str

    def __post_init__(self) -> None:
        _validate_region_month(self.region, self.month)
        if self.dataset not in DATASETS:
            raise ValueError(f"不支援的資料集：{self.dataset}")


@dataclass(frozen=True)
class StoredArtifact:
    metadata: ArtifactMetadata
    path: Path
    sha256: str


@dataclass(frozen=True)
class ArtifactRecord:
    id: int
    stored: StoredArtifact
    parse_status: str
    parse_error: str | None = None


@dataclass(frozen=True)
class RunResult:
    export_dir: Path | None
    warnings: tuple[str, ...]
    status: str

    def __post_init__(self) -> None:
        if self.status not in RUN_STATUSES:
            raise ValueError(f"不支援的執行狀態：{self.status}")


@dataclass(frozen=True, order=True)
class GapItem:
    region: str
    dataset: str
    roc_year: int
    month: int


@dataclass(frozen=True)
class GapReport:
    missing: tuple[GapItem, ...] = ()
    unsupported_media: tuple[GapItem, ...] = ()
