from __future__ import annotations

import csv
from io import BytesIO, StringIO
from pathlib import PurePosixPath
import re
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from .models import SnapshotValidationError


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS = {"main": MAIN_NS, "rel": REL_NS, "r": OFFICE_REL_NS}


def decode_csv_rows(data: bytes) -> list[list[str]]:
    last_error: UnicodeDecodeError | None = None
    for encoding in ("utf-8-sig", "cp950"):
        try:
            text = data.decode(encoding)
            return [list(row) for row in csv.reader(StringIO(text))]
        except UnicodeDecodeError as exc:
            last_error = exc
    raise SnapshotValidationError(f"CSV 編碼無法辨識：{last_error}")


def _column_index(cell_reference: str) -> int:
    match = re.match(r"([A-Z]+)", cell_reference)
    if not match:
        raise SnapshotValidationError(f"XLSX 儲存格座標無效：{cell_reference}")
    value = 0
    for character in match.group(1):
        value = value * 26 + ord(character) - 64
    return value - 1


def _shared_strings(archive: ZipFile) -> list[str]:
    try:
        root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return [
        "".join(node.text or "" for node in item.findall(".//main:t", NS))
        for item in root.findall("main:si", NS)
    ]


def _first_sheet_path(archive: ZipFile) -> str:
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    relationships = ElementTree.fromstring(
        archive.read("xl/_rels/workbook.xml.rels")
    )
    first_sheet = workbook.find("main:sheets/main:sheet", NS)
    if first_sheet is None:
        raise SnapshotValidationError("XLSX 缺少 worksheet")
    relationship_id = first_sheet.attrib.get(f"{{{OFFICE_REL_NS}}}id")
    for relation in relationships.findall("rel:Relationship", NS):
        if relation.attrib.get("Id") == relationship_id:
            target = relation.attrib["Target"].lstrip("/")
            if target.startswith("xl/"):
                return target
            return str(PurePosixPath("xl") / target)
    raise SnapshotValidationError("XLSX worksheet relationship 缺失")


def _cell_value(cell: ElementTree.Element, shared: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//main:t", NS))
    value = cell.findtext("main:v", default="", namespaces=NS)
    if cell_type == "s" and value:
        return shared[int(value)]
    return value


def read_xlsx_rows(data: bytes) -> list[list[str]]:
    try:
        with ZipFile(BytesIO(data)) as archive:
            shared = _shared_strings(archive)
            sheet_path = _first_sheet_path(archive)
            root = ElementTree.fromstring(archive.read(sheet_path))
            rows: list[list[str]] = []
            for row in root.findall(".//main:sheetData/main:row", NS):
                values: list[str] = []
                for cell in row.findall("main:c", NS):
                    index = _column_index(cell.attrib.get("r", ""))
                    while len(values) <= index:
                        values.append("")
                    values[index] = _cell_value(cell, shared)
                rows.append(values)
            return rows
    except SnapshotValidationError:
        raise
    except (BadZipFile, KeyError, ValueError, ElementTree.ParseError) as exc:
        raise SnapshotValidationError(f"XLSX 格式無法解析：{exc}") from exc
