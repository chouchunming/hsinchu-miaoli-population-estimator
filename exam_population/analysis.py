from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from .estimator import estimate_birth_cohort, estimate_exam_year
from .models import REGIONS, MigrationSnapshot, PopulationSnapshot


METHOD_DISCLAIMER = "單歲戶籍人口按出生月份重疊比例加權；非實際報考人數"
MIGRATION_SCOPE = (
    "戶籍遷入／遷出登記總數；包含縣市內跨區移動；非跨縣市移入人口"
)
GRADE_OFFSETS = (
    ("幼幼班", 3),
    ("幼兒園小班", 4),
    ("幼兒園中班", 5),
    ("幼兒園大班", 6),
    ("國小一年級", 7),
    ("國小二年級", 8),
    ("國小三年級", 9),
    ("國小四年級", 10),
    ("國小五年級", 11),
    ("國小六年級", 12),
    ("國中一年級", 13),
    ("國中二年級", 14),
)


def _year_month(year: int, month: int) -> str:
    return f"{year}-{month:02d}"


def _birth_interval(start_year: int) -> str:
    return f"{start_year}-09～{start_year + 1}-08"


def exam_population_rows(repo, start_year: int, end_year: int) -> list[dict]:
    latest: dict[str, PopulationSnapshot] = {}
    for region in REGIONS:
        series = repo.population_series(region)
        if not series:
            raise ValueError(f"{region} 沒有可用的單歲人口 snapshot")
        latest[region] = max(series, key=lambda item: (item.roc_year, item.month))

    rows = []
    for exam_year in range(start_year, end_year + 1):
        row = {
            "會考年度": exam_year,
            "西元年度": exam_year + 1911,
            "出生區間": _birth_interval(exam_year - 16),
        }
        estimates = {}
        imputed = []
        for region in REGIONS:
            snapshot = latest[region]
            estimate = estimate_exam_year(snapshot, exam_year)
            estimates[region] = estimate.value
            row[f"{region}預估人數"] = estimate.value
            row[f"{region}資料年月"] = _year_month(
                snapshot.roc_year, snapshot.month
            )
            if estimate.imputed_future_months:
                imputed.append(
                    f"{region}補{estimate.imputed_future_months}月"
                )
        row["三區合計"] = sum(estimates.values())
        row["資料完整性"] = (
            f"暫估：{'、'.join(imputed)}" if imputed else "完整 cohort"
        )
        row["估算方法"] = METHOD_DISCLAIMER
        rows.append(row)
    return rows


def _compressed_months(months: set[int]) -> str:
    if not months:
        return ""
    ranges = []
    start = previous = min(months)
    for month in sorted(months)[1:]:
        if month == previous + 1:
            previous = month
            continue
        ranges.append((start, previous))
        start = previous = month
    ranges.append((start, previous))
    return "、".join(
        f"{start:02d}" if start == end else f"{start:02d}–{end:02d}"
        for start, end in ranges
    )


def annual_registered_migration_rows(repo) -> list[dict]:
    grouped: dict[tuple[int, str], list[MigrationSnapshot]] = defaultdict(list)
    years: set[int] = set()
    for region in REGIONS:
        for snapshot in repo.migration_series(region):
            grouped[(snapshot.roc_year, region)].append(snapshot)
            years.add(snapshot.roc_year)

    rows = []
    for year in sorted(years):
        regional_rows = []
        for region in REGIONS:
            snapshots = grouped.get((year, region), [])
            months = {item.month for item in snapshots}
            moved_in = sum(item.registered_moved_in_total for item in snapshots)
            moved_out = sum(item.registered_moved_out_total for item in snapshots)
            row = {
                "年度": year,
                "地區": region,
                "戶籍遷入登記總數": moved_in,
                "戶籍遷出登記總數": moved_out,
                "戶籍登記淨變化": moved_in - moved_out,
                "涵蓋月份": _compressed_months(months),
                "資料完整性": (
                    "完整年度" if months == set(range(1, 13)) else "部分年度"
                ),
                "統計口徑": MIGRATION_SCOPE,
            }
            rows.append(row)
            regional_rows.append(row)
        coverage = {row["涵蓋月份"] for row in regional_rows}
        rows.append(
            {
                "年度": year,
                "地區": "三區合計",
                "戶籍遷入登記總數": sum(
                    row["戶籍遷入登記總數"] for row in regional_rows
                ),
                "戶籍遷出登記總數": sum(
                    row["戶籍遷出登記總數"] for row in regional_rows
                ),
                "戶籍登記淨變化": sum(
                    row["戶籍登記淨變化"] for row in regional_rows
                ),
                "涵蓋月份": (
                    next(iter(coverage))
                    if len(coverage) == 1
                    else "；".join(
                        f"{row['地區']}:{row['涵蓋月份']}"
                        for row in regional_rows
                    )
                ),
                "資料完整性": (
                    "完整年度"
                    if all(
                        row["資料完整性"] == "完整年度"
                        for row in regional_rows
                    )
                    else "部分年度"
                ),
                "統計口徑": MIGRATION_SCOPE,
            }
        )
    return rows


def _comparison_periods(
    series: list[PopulationSnapshot],
) -> list[tuple[PopulationSnapshot | None, PopulationSnapshot]]:
    by_month = {(item.roc_year, item.month): item for item in series}
    if not by_month:
        return []
    periods: list[tuple[PopulationSnapshot | None, PopulationSnapshot]] = []
    endpoint_years = sorted({year for year, _month in by_month})
    latest_key = max(by_month)
    for year in endpoint_years:
        december = by_month.get((year, 12))
        prior_december = by_month.get((year - 1, 12))
        if december is not None:
            periods.append((prior_december, december))
    if latest_key[1] != 12:
        endpoint = by_month[latest_key]
        baseline = by_month.get((endpoint.roc_year - 1, endpoint.month))
        periods.append((baseline, endpoint))
    if not periods:
        endpoint = by_month[latest_key]
        periods.append((None, endpoint))
    return periods


def _percentage(delta: int, baseline: int) -> str:
    if baseline == 0:
        return ""
    percent = (Decimal(delta) * 100 / Decimal(baseline)).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    return f"{percent:.2f}%"


def grade_cohort_change_rows(repo) -> list[dict]:
    rows: list[dict] = []
    for region in REGIONS:
        for baseline, endpoint in _comparison_periods(repo.population_series(region)):
            school_year = (
                endpoint.roc_year if endpoint.month >= 9 else endpoint.roc_year - 1
            )
            period = (
                f"{endpoint.roc_year - 1}-{endpoint.month:02d}～"
                f"{endpoint.roc_year}-{endpoint.month:02d}"
            )
            for grade, offset in GRADE_OFFSETS:
                birth_year = school_year - offset
                end_value = estimate_birth_cohort(
                    endpoint, birth_year, 9
                ).value
                if baseline is None:
                    start_value = ""
                    delta = ""
                    rate = ""
                    completeness = "基期不足"
                else:
                    start_number = estimate_birth_cohort(
                        baseline, birth_year, 9
                    ).value
                    delta_number = end_value - start_number
                    start_value = start_number
                    delta = delta_number
                    rate = _percentage(delta_number, start_number)
                    completeness = (
                        "完整年度" if endpoint.month == 12 else "年中暫估"
                    )
                    if start_number == 0:
                        completeness += "；期初為零"
                rows.append(
                    {
                        "比較期間": period,
                        "學年度": school_year,
                        "地區": region,
                        "班別或年級": grade,
                        "出生區間": _birth_interval(birth_year),
                        "期初人口": start_value,
                        "期末人口": end_value,
                        "人口增減": delta,
                        "增減率": rate,
                        "資料完整性": completeness,
                    }
                )

    regional_by_key: dict[tuple[str, int, str], list[dict]] = defaultdict(list)
    for row in rows:
        regional_by_key[
            (row["比較期間"], row["學年度"], row["班別或年級"])
        ].append(row)
    totals = []
    for (period, school_year, grade), regional in regional_by_key.items():
        if {row["地區"] for row in regional} != set(REGIONS):
            continue
        baseline_available = all(row["期初人口"] != "" for row in regional)
        start_total = (
            sum(int(row["期初人口"]) for row in regional)
            if baseline_available
            else ""
        )
        end_total = sum(int(row["期末人口"]) for row in regional)
        delta_total = end_total - start_total if baseline_available else ""
        totals.append(
            {
                "比較期間": period,
                "學年度": school_year,
                "地區": "三區合計",
                "班別或年級": grade,
                "出生區間": regional[0]["出生區間"],
                "期初人口": start_total,
                "期末人口": end_total,
                "人口增減": delta_total,
                "增減率": (
                    _percentage(delta_total, start_total)
                    if baseline_available
                    else ""
                ),
                "資料完整性": (
                    regional[0]["資料完整性"]
                    if len({row["資料完整性"] for row in regional}) == 1
                    else "地區狀態不一致"
                ),
            }
        )
    return sorted(
        rows + totals,
        key=lambda row: (
            row["比較期間"],
            row["班別或年級"],
            (*REGIONS, "三區合計").index(row["地區"]),
        ),
    )
