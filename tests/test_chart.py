import csv
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from scripts.render_exam_population_chart import (
    ChartDataError,
    find_latest_exam_csv,
    load_chart_rows,
    main,
    render_svg,
    write_chart,
)


FIELDS = (
    "會考年度",
    "新竹縣預估人數",
    "新竹市預估人數",
    "苗栗縣預估人數",
    "三區合計",
    "資料完整性",
)


class ChartTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data_root = self.root / "population"

    def tearDown(self):
        self.temporary.cleanup()

    def rows(self):
        result = []
        for index, year in enumerate(range(116, 131)):
            county = 7024 - index * 100
            city = 5681 - index * 90
            miaoli = 5052 - index * 80
            result.append(
                {
                    "會考年度": year,
                    "新竹縣預估人數": county,
                    "新竹市預估人數": city,
                    "苗栗縣預估人數": miaoli,
                    "三區合計": county + city + miaoli,
                    "資料完整性": (
                        "暫估：新竹縣補2月、新竹市補2月、苗栗縣補2月"
                        if year == 130
                        else "完整 cohort"
                    ),
                }
            )
        return result

    def write_export(
        self,
        name,
        *,
        rows=None,
        omit_field=None,
    ):
        destination = self.data_root / "exports" / name
        destination.mkdir(parents=True)
        path = destination / "exam_population_116_130.csv"
        fields = tuple(field for field in FIELDS if field != omit_field)
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=fields,
                extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(rows or self.rows())
        return path

    def test_find_latest_exam_csv_uses_newest_export(self):
        older = self.write_export("20260101T000000.000000Z")
        newer = self.write_export("20260730T000000.000000Z")

        self.assertEqual(find_latest_exam_csv(self.data_root), newer)
        self.assertNotEqual(older, newer)

    def test_find_latest_exam_csv_rejects_missing_exports(self):
        with self.assertRaisesRegex(ChartDataError, "找不到會考人口 CSV"):
            find_latest_exam_csv(self.data_root)

    def test_load_chart_rows_reads_four_series_and_provisional_year(self):
        path = self.write_export("20260730T000000.000000Z")

        rows = load_chart_rows(path)

        self.assertEqual([row.exam_year for row in rows], list(range(116, 131)))
        self.assertEqual(rows[0].regional_values, (7024, 5681, 5052))
        self.assertEqual(rows[0].total, 17757)
        self.assertFalse(rows[0].provisional)
        self.assertTrue(rows[-1].provisional)

    def test_missing_required_column_is_rejected(self):
        path = self.write_export(
            "20260730T000000.000000Z",
            omit_field="三區合計",
        )

        with self.assertRaisesRegex(ChartDataError, "缺少必要欄位.*三區合計"):
            load_chart_rows(path)

    def test_duplicate_or_non_contiguous_years_are_rejected(self):
        rows = self.rows()
        rows[1]["會考年度"] = 116
        path = self.write_export("20260730T000000.000000Z", rows=rows)

        with self.assertRaisesRegex(ChartDataError, "必須連續且恰為 116–130"):
            load_chart_rows(path)

    def test_negative_and_non_integer_population_are_rejected(self):
        negative_rows = self.rows()
        negative_rows[0]["新竹縣預估人數"] = -1
        negative_path = self.write_export(
            "20260730T000000.000000Z",
            rows=negative_rows,
        )
        invalid_rows = self.rows()
        invalid_rows[0]["新竹市預估人數"] = "not-an-integer"
        invalid_path = self.write_export(
            "20260731T000000.000000Z",
            rows=invalid_rows,
        )

        with self.assertRaisesRegex(ChartDataError, "不得為負數"):
            load_chart_rows(negative_path)
        with self.assertRaisesRegex(ChartDataError, "不是整數"):
            load_chart_rows(invalid_path)

    def test_total_must_equal_three_regions(self):
        rows = self.rows()
        rows[0]["三區合計"] += 1
        path = self.write_export("20260730T000000.000000Z", rows=rows)

        with self.assertRaisesRegex(ChartDataError, "不等於三地加總"):
            load_chart_rows(path)

    def test_only_year_130_may_be_provisional(self):
        rows = self.rows()
        rows[0]["資料完整性"] = "暫估"
        path = self.write_export("20260730T000000.000000Z", rows=rows)

        with self.assertRaisesRegex(ChartDataError, "只有民國 130 年"):
            load_chart_rows(path)

    def test_year_130_must_be_provisional(self):
        rows = self.rows()
        rows[-1]["資料完整性"] = "完整 cohort"
        path = self.write_export("20260730T000000.000000Z", rows=rows)

        with self.assertRaisesRegex(ChartDataError, "只有民國 130 年"):
            load_chart_rows(path)

    def test_render_svg_contains_four_series_values_and_accessible_text(self):
        rows = load_chart_rows(
            self.write_export("20260730T000000.000000Z")
        )

        svg = render_svg(rows)

        self.assertIn(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 980"',
            svg,
        )
        self.assertIn(
            '<title id="chart-title">竹竹苗國三會考應屆人口推估</title>',
            svg,
        )
        self.assertIn('<desc id="chart-desc">', svg)
        for series in (
            "hsinchu-county",
            "hsinchu-city",
            "miaoli-county",
            "total",
        ):
            self.assertEqual(
                svg.count(f'data-series="{series}" data-year='),
                15,
            )
            self.assertIn(f'data-series-line="{series}"', svg)
        self.assertIn(">17,757<", svg)
        self.assertIn("暫估：各地補 2 月", svg)
        self.assertIn(
            'data-year="130" data-provisional="true"',
            svg,
        )

    def test_render_svg_labels_axes_and_estimate_limitations(self):
        rows = load_chart_rows(
            self.write_export("20260730T000000.000000Z")
        )

        svg = render_svg(rows)

        self.assertIn("預估人數（人）", svg)
        self.assertIn("民國會考年度", svg)
        self.assertIn("非實際畢業或報考人數", svg)
        self.assertIn("stroke-dasharray", svg)
        self.assertEqual(svg.count('data-series-line="'), 4)

    def test_write_chart_does_not_leave_output_after_invalid_csv(self):
        rows = self.rows()
        rows[0]["新竹縣預估人數"] = "invalid"
        invalid = self.write_export(
            "20260730T000000.000000Z",
            rows=rows,
        )
        output = self.root / "chart.svg"

        with self.assertRaises(ChartDataError):
            write_chart(invalid, output)

        self.assertFalse(output.exists())

    def test_main_writes_explicit_input_and_reports_missing_default(self):
        source = self.write_export("20260730T000000.000000Z")
        output = self.root / "chart.svg"
        stdout = StringIO()
        stderr = StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            success = main(
                [
                    "--input",
                    str(source),
                    "--output",
                    str(output),
                ]
            )
            failure = main(
                [
                    "--data-root",
                    str(self.root / "missing"),
                    "--output",
                    str(self.root / "not-written.svg"),
                ]
            )

        self.assertEqual(success, 0)
        self.assertTrue(output.exists())
        self.assertIn(str(output), stdout.getvalue())
        self.assertEqual(failure, 1)
        self.assertIn("錯誤：找不到會考人口 CSV", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
