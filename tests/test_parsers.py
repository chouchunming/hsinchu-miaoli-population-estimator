import csv
from io import BytesIO
from io import StringIO
import unittest
from zipfile import ZIP_DEFLATED, ZipFile

from exam_population.models import SnapshotValidationError
from exam_population.parsers import (
    parse_age_csv,
    parse_age_rows,
    parse_age_xlsx,
    parse_migration_csv,
    parse_migration_rows,
    parse_migration_xlsx,
)


def xlsx_bytes(rows):
    def cell(ref, value):
        if isinstance(value, int):
            return f'<c r="{ref}"><v>{value}</v></c>'
        escaped = (
            str(value)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        return f'<c r="{ref}" t="inlineStr"><is><t>{escaped}</t></is></c>'

    def column_name(index):
        result = ""
        while index:
            index, remainder = divmod(index - 1, 26)
            result = chr(65 + remainder) + result
        return result

    sheet_rows = []
    for row_number, row in enumerate(rows, 1):
        cells = "".join(
            cell(f"{column_name(column)}{row_number}", value)
            for column, value in enumerate(row, 1)
            if value != ""
        )
        sheet_rows.append(f'<row r="{row_number}">{cells}</row>')
    sheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(sheet_rows)}</sheetData></worksheet>"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    relationships = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/></Relationships>'
    )
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", relationships)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return output.getvalue()


def csv_bytes(rows, encoding="utf-8-sig"):
    output = StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerows(rows)
    return output.getvalue().encode(encoding)


AGE_ROWS = [
    ["中華民國115年06月底"],
    ["區域別", "性別", "總計", "人口數_0歲", "人口數_13歲", "人口數_14歲", "人口數_100歲以上"],
    ["竹北市", "計", 220000, 1000, 5000, 5100, 100],
    ["總計", "計", 600000, 2374, 5400, 5527, 400],
]

TWO_ROW_AGE_HEADERS = [
    ["中華民國115年06月底"],
    ["區域代碼", "區域別", "性別", "總計", "0～4歲", "", ""],
    ["", "", "", "", "合計", "0歲", "1歲"],
    ["10004000", "總　計", "計", 598260, 21873, 2802, 4136],
]

MIGRATION_ROWS = [
    ["中華民國115年06月"],
    ["區域別", "性別", "遷入人數_合計", "遷入人數_自本縣(市)他鄉(鎮市區)", "遷出人數_合計"],
    ["竹南鎮", "計", 300, 100, 250],
    ["總計", "計", "1,880", 450, "1,660"],
]


class ParserTests(unittest.TestCase):
    def test_age_csv_uses_total_row_and_single_age_columns(self):
        snapshot = parse_age_csv(csv_bytes(AGE_ROWS), "新竹市")
        self.assertEqual((snapshot.roc_year, snapshot.month), (115, 6))
        self.assertEqual(snapshot.age_population, {0: 2374, 13: 5400, 14: 5527})

    def test_cp950_csv_is_supported(self):
        self.assertEqual(
            parse_age_csv(csv_bytes(AGE_ROWS, "cp950"), "新竹市").age_population[0],
            2374,
        )

    def test_xlsx_inline_strings_and_numeric_cells_parse(self):
        snapshot = parse_age_xlsx(xlsx_bytes(AGE_ROWS), "苗栗縣")
        self.assertEqual(snapshot.age_population[14], 5527)

    def test_age_parser_supports_region_and_age_headers_on_adjacent_rows(self):
        snapshot = parse_age_rows(TWO_ROW_AGE_HEADERS, "新竹縣")
        self.assertEqual(snapshot.age_population, {0: 2802, 1: 4136})

    def test_miaoli_date_without_month_end_and_exact_total_headers_parse(self):
        snapshot = parse_migration_xlsx(xlsx_bytes(MIGRATION_ROWS), "苗栗縣")
        self.assertEqual((snapshot.roc_year, snapshot.month), (115, 6))
        self.assertEqual(snapshot.registered_moved_in_total, 1880)
        self.assertEqual(snapshot.registered_moved_out_total, 1660)

    def test_migration_csv_selects_region_total(self):
        snapshot = parse_migration_csv(csv_bytes(MIGRATION_ROWS), "新竹市")
        self.assertEqual(snapshot.registered_moved_in_total, 1880)

    def test_blank_migration_total_fails(self):
        rows = [row[:] for row in MIGRATION_ROWS]
        rows[-1][2] = ""
        with self.assertRaisesRegex(SnapshotValidationError, "遷入"):
            parse_migration_rows(rows, "新竹縣")

    def test_duplicate_age_header_fails(self):
        rows = [row[:] for row in AGE_ROWS]
        rows[1].insert(6, "另一欄_14歲")
        rows[2].insert(6, 1)
        rows[3].insert(6, 2)
        with self.assertRaisesRegex(SnapshotValidationError, "重複年齡"):
            parse_age_rows(rows, "新竹縣")

    def test_malformed_xlsx_fails_with_format_name(self):
        with self.assertRaisesRegex(SnapshotValidationError, "XLSX"):
            parse_age_xlsx(b"not a zip", "苗栗縣")


if __name__ == "__main__":
    unittest.main()
