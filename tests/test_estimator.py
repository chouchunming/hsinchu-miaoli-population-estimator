import unittest

from exam_population.estimator import estimate_birth_cohort, estimate_exam_year
from exam_population.models import PopulationSnapshot, RegionEstimate, SnapshotValidationError


def population(values, *, region="新竹縣", year=115, month=6):
    return PopulationSnapshot(
        artifact_id=None,
        region=region,
        roc_year=year,
        month=month,
        age_population=values,
    )


class EstimatorTests(unittest.TestCase):
    def test_june_snapshot_weights_exam_116_across_adjacent_ages(self):
        snapshot = population({13: 1200, 14: 2400})
        self.assertEqual(estimate_exam_year(snapshot, 116), RegionEstimate(2200, 0))

    def test_future_tail_uses_age_zero_month_average(self):
        snapshot = population({0: 1200})
        self.assertEqual(estimate_exam_year(snapshot, 130), RegionEstimate(1200, 2))

    def test_observed_missing_age_fails(self):
        with self.assertRaisesRegex(SnapshotValidationError, "缺少年齡"):
            estimate_exam_year(population({14: 100}), 116)

    def test_half_rounds_up(self):
        self.assertEqual(estimate_exam_year(population({13: 3, 14: 0}), 116).value, 1)

    def test_generic_cohort_accepts_september_start(self):
        result = estimate_birth_cohort(population({13: 1200, 14: 2400}), 100, 9)
        self.assertEqual(result.value, 2200)


class PopulationValidationTests(unittest.TestCase):
    def test_invalid_region_is_rejected(self):
        with self.assertRaisesRegex(SnapshotValidationError, "不支援"):
            population({0: 1}, region="台北市")

    def test_negative_population_is_rejected(self):
        with self.assertRaisesRegex(SnapshotValidationError, "非負整數"):
            population({0: -1})


if __name__ == "__main__":
    unittest.main()
