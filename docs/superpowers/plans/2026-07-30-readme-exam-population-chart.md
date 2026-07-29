# README Exam Population Chart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a reproducible SVG chart from the latest 116–130 exam-population CSV and embed the chart in the GitHub README.

**Architecture:** A standalone standard-library script finds or accepts an export CSV, validates it into immutable chart rows, renders deterministic SVG, and atomically writes the stable README image path. Focused `unittest` coverage protects selection, validation, SVG semantics, failure behavior, and README integration.

**Tech Stack:** Python 3.11+ standard library, CSV, dataclasses, SVG, `unittest`

## Global Constraints

- Use only the Python standard library; add no plotting dependency.
- Default input is the newest `data/population/exports/*/exam_population_116_130.csv`.
- Default output is `docs/images/exam-population-116-130.svg`.
- Render four series: 新竹縣、新竹市、苗栗縣、三區合計.
- Render exactly the ROC exam years 116–130 and visibly distinguish year 130 as provisional.
- Treat the values as household-registration cohort estimates, not actual examinee counts.
- Do not mutate CSV, SQLite, or raw population archives.
- Add the OSI standard MIT License with `Copyright (c) 2026 Vincent Chou`.
- Run every test, compilation, validation, rendering, and Git command under `caffeinate -i -m`.
- Commit and push each independently reviewable unit to `feature/skip-existing-downloads`.

---

### Task 1: Validated Chart Data Loader

**Files:**
- Create: `scripts/render_exam_population_chart.py`
- Create: `tests/test_chart.py`

**Interfaces:**
- Consumes: `Path` to `data/population` or an explicit export CSV
- Produces: `ChartDataError`, `ChartRow`, `find_latest_exam_csv(data_root: Path) -> Path`, `load_chart_rows(csv_path: Path) -> tuple[ChartRow, ...]`

- [ ] **Step 1: Write failing loader and validation tests**

Create `tests/test_chart.py` with a helper that writes 15 `utf-8-sig` rows for
years 116–130. Add tests equivalent to:

```python
def test_find_latest_exam_csv_uses_newest_export(self):
    older = self.write_export("20260101T000000.000000Z")
    newer = self.write_export("20260730T000000.000000Z")
    self.assertEqual(find_latest_exam_csv(self.data_root), newer)
    self.assertNotEqual(older, newer)

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
```

Also cover duplicate or non-contiguous years, negative/non-integer population,
total not equal to the three regions, and year 130 without `暫估` in
`資料完整性`.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
caffeinate -i -m python3 -m unittest tests.test_chart -v
```

Expected: import failure because `scripts.render_exam_population_chart` does not
exist.

- [ ] **Step 3: Implement the data types, latest-file selection, and parser**

In `scripts/render_exam_population_chart.py`, define the exact public boundary:

```python
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import html
import math
from pathlib import Path
import sys
import tempfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class ChartDataError(ValueError):
    pass


@dataclass(frozen=True)
class ChartRow:
    exam_year: int
    hsinchu_county: int
    hsinchu_city: int
    miaoli_county: int
    total: int
    completeness: str

    @property
    def regional_values(self) -> tuple[int, int, int]:
        return (self.hsinchu_county, self.hsinchu_city, self.miaoli_county)

    @property
    def provisional(self) -> bool:
        return "暫估" in self.completeness


def find_latest_exam_csv(data_root: Path) -> Path:
    candidates = sorted(
        Path(data_root).glob("exports/*/exam_population_116_130.csv")
    )
    if not candidates:
        raise ChartDataError(f"找不到會考人口 CSV：{data_root}")
    return candidates[-1]


def load_chart_rows(csv_path: Path) -> tuple[ChartRow, ...]:
    required = (
        "會考年度",
        "新竹縣預估人數",
        "新竹市預估人數",
        "苗栗縣預估人數",
        "三區合計",
        "資料完整性",
    )
    try:
        stream = Path(csv_path).open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise ChartDataError(f"無法讀取會考人口 CSV：{csv_path}：{exc}") from exc
    with stream:
        reader = csv.DictReader(stream)
        fieldnames = tuple(reader.fieldnames or ())
        missing = tuple(field for field in required if field not in fieldnames)
        if missing:
            raise ChartDataError(f"缺少必要欄位：{', '.join(missing)}")
        rows = []
        for line_number, raw in enumerate(reader, start=2):
            values = {}
            for field in required[:-1]:
                try:
                    value = int((raw.get(field) or "").strip())
                except ValueError as exc:
                    raise ChartDataError(
                        f"第 {line_number} 列 {field} 不是整數"
                    ) from exc
                if value < 0:
                    raise ChartDataError(
                        f"第 {line_number} 列 {field} 不得為負數"
                    )
                values[field] = value
            row = ChartRow(
                exam_year=values["會考年度"],
                hsinchu_county=values["新竹縣預估人數"],
                hsinchu_city=values["新竹市預估人數"],
                miaoli_county=values["苗栗縣預估人數"],
                total=values["三區合計"],
                completeness=(raw.get("資料完整性") or "").strip(),
            )
            if row.total != sum(row.regional_values):
                raise ChartDataError(
                    f"第 {line_number} 列三區合計不等於三地加總"
                )
            rows.append(row)
    expected_years = tuple(range(116, 131))
    if tuple(row.exam_year for row in rows) != expected_years:
        raise ChartDataError("會考年度必須連續且恰為 116–130")
    if any(row.provisional for row in rows[:-1]) or not rows[-1].provisional:
        raise ChartDataError("只有民國 130 年必須標示為暫估")
    return tuple(rows)
```

Use explicit field constants and errors that name the file, row, and invalid
field. Do not silently skip malformed rows.

- [ ] **Step 4: Run focused and existing tests**

Run:

```bash
caffeinate -i -m python3 -m unittest tests.test_chart -v
caffeinate -i -m python3 -W error::ResourceWarning -m unittest discover \
  -s tests -p 'test_*.py' -v
```

Expected: all loader tests and the existing 52 tests pass.

- [ ] **Step 5: Review, commit, and push Task 1**

Run:

```bash
caffeinate -i -m git diff --check
caffeinate -i -m git add scripts/render_exam_population_chart.py tests/test_chart.py
caffeinate -i -m git commit -m "Add validated exam chart data loader"
caffeinate -i -m git push
```

Expected: a clean Task 1 commit is reachable from
`origin/feature/skip-existing-downloads`.

---

### Task 2: Accessible Deterministic SVG Renderer

**Files:**
- Modify: `scripts/render_exam_population_chart.py`
- Modify: `tests/test_chart.py`

**Interfaces:**
- Consumes: `tuple[ChartRow, ...]`
- Produces: `render_svg(rows: tuple[ChartRow, ...]) -> str`, `write_chart(csv_path: Path, output_path: Path) -> Path`, `main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: Write failing SVG and atomic-failure tests**

Add tests equivalent to:

```python
def test_render_svg_contains_four_series_values_and_accessible_text(self):
    rows = load_chart_rows(self.write_export("20260730T000000.000000Z"))
    svg = render_svg(rows)
    self.assertIn("<title>竹竹苗國三會考應屆人口推估</title>", svg)
    self.assertIn("<desc>", svg)
    for series in ("hsinchu-county", "hsinchu-city", "miaoli-county", "total"):
        self.assertEqual(svg.count(f'data-series="{series}" data-year='), 15)
    self.assertIn(">17,757<", svg)
    self.assertIn("暫估：各地補 2 月", svg)
    self.assertIn('data-year="130" data-provisional="true"', svg)

def test_write_chart_does_not_leave_output_after_invalid_csv(self):
    invalid = self.write_export(
        "20260730T000000.000000Z",
        invalid_value=("新竹縣預估人數", "invalid"),
    )
    output = self.root / "chart.svg"
    with self.assertRaises(ChartDataError):
        write_chart(invalid, output)
    self.assertFalse(output.exists())
```

Also verify the SVG has a `viewBox`, year and population axis labels, the
non-examinee disclaimer, and distinct line style metadata for all series.

- [ ] **Step 2: Run renderer tests and verify RED**

Run:

```bash
caffeinate -i -m python3 -m unittest \
  tests.test_chart.ChartTests.test_render_svg_contains_four_series_values_and_accessible_text \
  tests.test_chart.ChartTests.test_write_chart_does_not_leave_output_after_invalid_csv \
  -v
```

Expected: import failure because `render_svg` and `write_chart` are undefined.

- [ ] **Step 3: Implement deterministic SVG geometry and labels**

Add:

```python
SERIES = (
    ("hsinchu-county", "新竹縣", "hsinchu_county", "#0072B2", ""),
    ("hsinchu-city", "新竹市", "hsinchu_city", "#D55E00", "9 5"),
    ("miaoli-county", "苗栗縣", "miaoli_county", "#009E73", "3 4"),
    ("total", "三區合計", "total", "#7A3E9D", ""),
)


def render_svg(rows: tuple[ChartRow, ...]) -> str:
    width, height = 1600, 980
    left, right, top, bottom = 130, 70, 170, 135
    plot_width = width - left - right
    plot_height = height - top - bottom
    y_step = 2_000
    y_max = max(
        y_step,
        math.ceil(max(row.total for row in rows) / y_step) * y_step,
    )

    def x_position(index: int) -> float:
        return left + index * plot_width / (len(rows) - 1)

    def y_position(value: int) -> float:
        return top + plot_height * (1 - value / y_max)

    parts = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {width} {height}" role="img" '
            f'aria-labelledby="chart-title chart-desc">'
        ),
        '<title id="chart-title">竹竹苗國三會考應屆人口推估</title>',
        (
            '<desc id="chart-desc">民國 116 至 130 年新竹縣、新竹市、'
            '苗栗縣與三區合計的戶籍人口 cohort 推估；民國 130 年為暫估。'
            '</desc>'
        ),
        """<style>
        text { font-family: "PingFang TC", "Noto Sans TC", sans-serif; fill: #25313c; }
        .grid { stroke: #d7dee5; stroke-width: 1; }
        .axis { stroke: #52606d; stroke-width: 2; }
        .tick { font-size: 18px; fill: #52606d; }
        .value { font-size: 15px; font-weight: 500; paint-order: stroke;
                 stroke: #fff; stroke-width: 4px; stroke-linejoin: round; }
        .legend { font-size: 21px; font-weight: 500; }
        .note { font-size: 18px; fill: #52606d; }
        </style>""",
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        '<text x="80" y="60" font-size="34" font-weight="500">'
        '竹竹苗國三會考應屆人口推估</text>',
        '<text x="80" y="98" class="note">'
        '單歲戶籍人口按出生月份重疊比例加權；非實際畢業或報考人數</text>',
    ]
    for value in range(0, y_max + y_step, y_step):
        y = y_position(value)
        parts.extend(
            (
                f'<line class="grid" x1="{left}" y1="{y:.1f}" '
                f'x2="{width - right}" y2="{y:.1f}"/>',
                f'<text class="tick" x="{left - 18}" y="{y + 6:.1f}" '
                f'text-anchor="end">{value:,}</text>',
            )
        )
    parts.extend(
        (
            f'<line class="axis" x1="{left}" y1="{top}" x2="{left}" '
            f'y2="{height - bottom}"/>',
            f'<line class="axis" x1="{left}" y1="{height - bottom}" '
            f'x2="{width - right}" y2="{height - bottom}"/>',
            '<text class="tick" x="28" y="510" transform="rotate(-90 28 510)">'
            '預估人數（人）</text>',
            f'<text class="tick" x="{width / 2}" y="{height - 35}" '
            f'text-anchor="middle">民國會考年度</text>',
        )
    )
    for index, row in enumerate(rows):
        x = x_position(index)
        parts.extend(
            (
                f'<line class="grid" x1="{x:.1f}" y1="{top}" '
                f'x2="{x:.1f}" y2="{height - bottom}"/>',
                f'<text class="tick" x="{x:.1f}" y="{height - bottom + 34}" '
                f'text-anchor="middle">{row.exam_year}</text>',
            )
        )
    label_offsets = (-12, 21, 38, -18)
    for series_index, (slug, label, attribute, color, dash) in enumerate(SERIES):
        values = tuple(getattr(row, attribute) for row in rows)
        points = " ".join(
            f"{x_position(index):.1f},{y_position(value):.1f}"
            for index, value in enumerate(values)
        )
        width_value = 5 if slug == "total" else 3
        dash_attribute = f' stroke-dasharray="{dash}"' if dash else ""
        parts.append(
            f'<polyline data-series-line="{slug}" points="{points}" '
            f'fill="none" stroke="{color}" stroke-width="{width_value}"'
            f'{dash_attribute}/>'
        )
        for index, (row, value) in enumerate(zip(rows, values, strict=True)):
            x, y = x_position(index), y_position(value)
            provisional = str(row.provisional).lower()
            fill = "#ffffff" if row.provisional else color
            parts.extend(
                (
                    f'<circle data-series="{slug}" data-year="{row.exam_year}" '
                    f'data-provisional="{provisional}" cx="{x:.1f}" cy="{y:.1f}" '
                    f'r="{7 if slug == "total" else 5}" fill="{fill}" '
                    f'stroke="{color}" stroke-width="3"/>',
                    f'<text class="value" x="{x:.1f}" '
                    f'y="{y + label_offsets[series_index]:.1f}" '
                    f'text-anchor="middle">{value:,}</text>',
                )
            )
    legend_x = 750
    for index, (slug, label, _attribute, color, dash) in enumerate(SERIES):
        x = legend_x + index * 190
        dash_attribute = f' stroke-dasharray="{dash}"' if dash else ""
        parts.extend(
            (
                f'<line x1="{x}" y1="93" x2="{x + 42}" y2="93" '
                f'stroke="{color}" stroke-width="{5 if slug == "total" else 3}"'
                f'{dash_attribute}/>',
                f'<text class="legend" x="{x + 52}" y="100">{html.escape(label)}</text>',
            )
        )
    parts.extend(
        (
            f'<text class="note" x="{width - right}" y="{top - 25}" '
            f'text-anchor="end">○ 民國 130 年暫估：各地補 2 月</text>',
            '</svg>',
        )
    )
    return "\n".join(parts) + "\n"


def write_chart(csv_path: Path, output_path: Path) -> Path:
    rows = load_chart_rows(csv_path)
    svg = render_svg(rows)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=output_path.parent,
        prefix=f".{output_path.name}.",
        suffix=".tmp",
        delete=False,
    ) as stream:
        stream.write(svg)
        temporary = Path(stream.name)
    try:
        temporary.replace(output_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return output_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="產生竹竹苗會考人口推估 SVG")
    parser.add_argument(
        "--data-root",
        type=Path,
        default=REPOSITORY_ROOT / "data" / "population",
    )
    parser.add_argument("--input", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=REPOSITORY_ROOT
        / "docs"
        / "images"
        / "exam-population-116-130.svg",
    )
    arguments = parser.parse_args(argv)
    try:
        csv_path = arguments.input or find_latest_exam_csv(arguments.data_root)
        destination = write_chart(csv_path, arguments.output)
    except ChartDataError as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 1
    print(destination)
    return 0
```

Add an `argparse` CLI with `--data-root`, optional `--input`, and `--output`.
Resolve all default paths from `REPOSITORY_ROOT`, print the written path on
success, and print `錯誤：...` to stderr with exit code 1 for `ChartDataError`.

- [ ] **Step 4: Run focused and full tests**

Run:

```bash
caffeinate -i -m python3 -m unittest tests.test_chart -v
caffeinate -i -m python3 -W error::ResourceWarning -m unittest discover \
  -s tests -p 'test_*.py' -v
caffeinate -i -m python3 -m compileall -q exam_population scripts tests
```

Expected: all tests pass and compilation exits zero.

- [ ] **Step 5: Review, commit, and push Task 2**

Run:

```bash
caffeinate -i -m git diff --check
caffeinate -i -m git add scripts/render_exam_population_chart.py tests/test_chart.py
caffeinate -i -m git commit -m "Render accessible exam population SVG"
caffeinate -i -m git push
```

Expected: the SVG renderer commit is reachable from the remote feature branch.

---

### Task 3: Generate the Chart and Embed It in README

**Files:**
- Create: `docs/images/exam-population-116-130.svg`
- Modify: `README.md`
- Modify: `tests/test_chart.py`

**Interfaces:**
- Consumes: `scripts/render_exam_population_chart.py` and the latest committed CSV
- Produces: the stable README SVG and a documented regeneration command

- [ ] **Step 1: Write the failing README integration test**

Add:

```python
def test_readme_embeds_generated_chart_and_links_source_csv(self):
    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
    self.assertIn(
        "![竹竹苗國三會考應屆人口推估]"
        "(docs/images/exam-population-116-130.svg)",
        readme,
    )
    self.assertIn("exam_population_116_130.csv", readme)
    self.assertIn("render_exam_population_chart.py", readme)
```

- [ ] **Step 2: Run the README test and verify RED**

Run:

```bash
caffeinate -i -m python3 -m unittest \
  tests.test_chart.ChartTests.test_readme_embeds_generated_chart_and_links_source_csv \
  -v
```

Expected: FAIL because README does not yet embed the SVG.

- [ ] **Step 3: Generate the chart and update README**

Run:

```bash
caffeinate -i -m python3 scripts/render_exam_population_chart.py
```

Add a `## 會考應屆人口推估` section after the opening limitations. Include:

````markdown
![竹竹苗國三會考應屆人口推估](docs/images/exam-population-116-130.svg)

資料表：[民國 116–130 年會考人口推估](data/population/exports/20260729T145221.972744Z/exam_population_116_130.csv)

重新產圖：

```bash
python3 scripts/render_exam_population_chart.py
```
````

State that year 130 is provisional and that all values are registered-population
cohort estimates rather than actual graduates or examinees.

- [ ] **Step 4: Run automated and visual verification**

Run:

```bash
caffeinate -i -m python3 -m unittest tests.test_chart -v
caffeinate -i -m python3 -W error::ResourceWarning -m unittest discover \
  -s tests -p 'test_*.py' -v
caffeinate -i -m python3 -m compileall -q exam_population scripts tests
caffeinate -i -m python3 scripts/validate_exam_population_artifacts.py \
  --data-root data/population
caffeinate -i -m xmllint --noout docs/images/exam-population-116-130.svg
caffeinate -i -m git diff --check
```

Render the SVG to a temporary PNG with macOS Quick Look, inspect it, and verify
that no title, axis, series label, point label, legend, or year-130 annotation
overlaps or clips.

Expected: all automated gates pass, artifact validation prints
`live artifact validation: PASS`, and visual inspection is legible.

- [ ] **Step 5: Inline review, commit, push, and verify GitHub**

Review the complete diff against the design spec, then run:

```bash
caffeinate -i -m git add README.md tests/test_chart.py \
  docs/images/exam-population-116-130.svg
caffeinate -i -m git commit -m "Embed exam population chart in README"
caffeinate -i -m git push
caffeinate -i -m git ls-remote --heads origin feature/skip-existing-downloads
caffeinate -i -m git status --short --branch
```

Expected: the remote feature branch points to the new commit and the working
tree is clean.

---

### Task 4: Add the MIT License Declaration

**Files:**
- Create: `LICENSE`
- Create: `tests/test_license.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: the OSI MIT License template and the approved holder name
- Produces: an OSI-recognizable root license and a README license link

- [ ] **Step 1: Write the failing license test**

Create `tests/test_license.py`:

```python
from pathlib import Path
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class LicenseTests(unittest.TestCase):
    def test_repository_declares_mit_license(self):
        license_text = (REPOSITORY_ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertTrue(license_text.startswith("MIT License\\n"))
        self.assertIn("Copyright (c) 2026 Vincent Chou", license_text)
        self.assertIn(
            "Permission is hereby granted, free of charge, to any person "
            "obtaining a copy",
            license_text,
        )
        self.assertIn('THE SOFTWARE IS PROVIDED "AS IS"', license_text)

    def test_readme_links_mit_license(self):
        readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("## 授權", readme)
        self.assertIn("[MIT License](LICENSE)", readme)
```

- [ ] **Step 2: Run the license tests and verify RED**

Run:

```bash
caffeinate -i -m python3 -m unittest tests.test_license -v
```

Expected: errors because the root `LICENSE` does not exist and README has no
license section.

- [ ] **Step 3: Add the canonical license and README declaration**

Create root `LICENSE` from the OSI MIT template with exactly:

```text
MIT License

Copyright (c) 2026 Vincent Chou

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

Add this README section:

```markdown
## 授權

本專案採用 [MIT License](LICENSE)，Copyright (c) 2026 Vincent Chou。
官方原始人口資料的權利與來源仍歸各發布機關所有。
```

- [ ] **Step 4: Run final automated verification**

Run:

```bash
caffeinate -i -m python3 -W error::ResourceWarning -m unittest discover \
  -s tests -p 'test_*.py' -v
caffeinate -i -m python3 -m compileall -q exam_population scripts tests
caffeinate -i -m python3 scripts/validate_exam_population_artifacts.py \
  --data-root data/population
caffeinate -i -m xmllint --noout docs/images/exam-population-116-130.svg
caffeinate -i -m git diff --check
```

Expected: all tests pass, artifact validation prints
`live artifact validation: PASS`, SVG validation exits zero, and the diff is
clean.

- [ ] **Step 5: Review, commit, push, and verify GitHub**

Review the complete diff against the design spec, then run:

```bash
caffeinate -i -m git add LICENSE README.md tests/test_license.py
caffeinate -i -m git commit -m "Declare MIT license"
caffeinate -i -m git push
caffeinate -i -m git ls-remote --heads origin feature/skip-existing-downloads
caffeinate -i -m git status --short --branch
```

Expected: the remote feature branch points to the final commit, the working tree
is clean, and GitHub recognizes the root `LICENSE` as MIT.
