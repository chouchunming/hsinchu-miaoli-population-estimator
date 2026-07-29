from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from .models import PopulationSnapshot, RegionEstimate, SnapshotValidationError


def month_index(roc_year: int, month: int) -> int:
    return roc_year * 12 + month - 1


def twelve_month_indexes(start_year: int, start_month: int) -> set[int]:
    start = month_index(start_year, start_month)
    return set(range(start, start + 12))


def age_birth_month_indexes(snapshot: PopulationSnapshot, age: int) -> set[int]:
    reference = month_index(snapshot.roc_year, snapshot.month)
    start = reference - (age + 1) * 12 + 1
    return set(range(start, start + 12))


def estimate_birth_cohort(
    snapshot: PopulationSnapshot,
    start_year: int,
    start_month: int,
) -> RegionEstimate:
    target = twelve_month_indexes(start_year, start_month)
    reference = month_index(snapshot.roc_year, snapshot.month)
    future = {item for item in target if item > reference}
    observed = target - future
    total = Decimal(0)
    covered: set[int] = set()
    for age, population in snapshot.age_population.items():
        overlap = observed & age_birth_month_indexes(snapshot, age)
        total += Decimal(population) * len(overlap) / 12
        covered |= overlap
    if covered != observed:
        raise SnapshotValidationError(
            f"{snapshot.region} {snapshot.roc_year}-{snapshot.month:02d} 缺少年齡資料"
        )
    if future:
        if 0 not in snapshot.age_population:
            raise SnapshotValidationError(f"{snapshot.region} 缺少 0 歲人口")
        total += Decimal(snapshot.age_population[0]) * len(future) / 12
    return RegionEstimate(
        int(total.quantize(Decimal("1"), rounding=ROUND_HALF_UP)),
        len(future),
    )


def estimate_exam_year(snapshot: PopulationSnapshot, exam_year: int) -> RegionEstimate:
    return estimate_birth_cohort(snapshot, exam_year - 16, 9)
