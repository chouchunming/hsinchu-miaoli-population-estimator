from __future__ import annotations

import base64
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
import re
import ssl
import time
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urljoin, urlparse
from urllib.request import Request, urlopen


class SourceDiscoveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class Link:
    url: str
    text: str
    title: str

    @property
    def label(self) -> str:
        return " ".join(part for part in (self.text, self.title) if part).strip()


@dataclass(frozen=True)
class DatasetCandidate:
    dataset: str
    region: str
    roc_year: int
    month: int
    source_page_url: str
    download_url: str
    original_filename: str
    extension: str
    media_type: str
    supported_for_parse: bool


class _LinkParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.links: list[Link] = []
        self._href: str | None = None
        self._title = ""
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        attributes = dict(attrs)
        self._href = attributes.get("href")
        self._title = attributes.get("title", "")
        self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() != "a" or self._href is None:
            return
        href = self._href
        self.links.append(
            Link(
                urljoin(self.base_url, href),
                " ".join("".join(self._text).split()),
                " ".join(self._title.split()),
            )
        )
        self._href = None
        self._title = ""
        self._text = []


class HttpClient:
    def __init__(self, *, timeout: float = 30.0, attempts: int = 3):
        self.timeout = timeout
        self.attempts = attempts
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.verify_flags &= ~ssl.VERIFY_X509_STRICT

    def get(self, url: str) -> bytes:
        request = Request(
            url,
            headers={
                "User-Agent": (
                    "hsinchu-miaoli-population-estimator/1.0 "
                    "(monthly official-statistics archiver)"
                )
            },
        )
        for attempt in range(1, self.attempts + 1):
            try:
                with urlopen(
                    request,
                    timeout=self.timeout,
                    context=self.ssl_context,
                ) as response:
                    return response.read()
            except HTTPError as exc:
                if exc.code != 429 and not 500 <= exc.code <= 599:
                    raise
                if attempt == self.attempts:
                    raise
            except URLError:
                if attempt == self.attempts:
                    raise
            time.sleep(0.25 * attempt)
        raise AssertionError("unreachable")


def _decode_html(data: bytes) -> str:
    for encoding in ("utf-8", "cp950"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    return data.decode("utf-8", errors="replace")


def _links(http, url: str) -> list[Link]:
    parser = _LinkParser(url)
    parser.feed(_decode_html(http.get(url)))
    return parser.links


ROC_TEXT_PATTERN = re.compile(r"(?<!\d)(\d{3})\s*年\s*(\d{1,2})\s*月")
ROC_FILE_PATTERN = re.compile(r"(?<!\d)(\d{3})(\d{2})(?:0[13])?(?!\d)")
MEDIA_TYPES = {
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pdf": "application/pdf",
}


def _roc_month(text: str) -> tuple[int, int] | None:
    decoded = unquote(text)
    match = ROC_TEXT_PATTERN.search(decoded)
    if match is None:
        match = ROC_FILE_PATTERN.search(decoded)
    if match is None:
        return None
    year, month = map(int, match.groups())
    return (year, month) if 1 <= month <= 12 else None


def _in_range(
    year_month: tuple[int, int],
    start: tuple[int, int],
    end: tuple[int, int],
) -> bool:
    return start <= year_month <= end


def _download_filename(url: str) -> str | None:
    query = parse_qs(urlparse(url).query)
    for parameter in ("filedisplay", "file"):
        for value in query.get(parameter, ()):
            name = Path(value).name
            if Path(name).suffix.lower() in MEDIA_TYPES:
                return name
    for parameter in ("n", "u"):
        for encoded in query.get(parameter, ()):
            try:
                padded = encoded + "=" * (-len(encoded) % 4)
                decoded = base64.b64decode(padded)
            except (ValueError, TypeError):
                continue
            for encoding in ("utf-8", "cp950"):
                try:
                    name = Path(decoded.decode(encoding)).name
                except UnicodeDecodeError:
                    continue
                if Path(name).suffix.lower() in MEDIA_TYPES:
                    return name
    return None


def _extension(url: str) -> str:
    download_filename = _download_filename(url)
    if download_filename is not None:
        return Path(download_filename).suffix.lower()
    return Path(urlparse(url).path).suffix.lower()


def _filename(url: str) -> str:
    return (
        _download_filename(url)
        or unquote(Path(urlparse(url).path).name)
        or "download.bin"
    )


def _candidate(
    *,
    link: Link,
    source_page_url: str,
    dataset: str,
    region: str,
    inherited_month: tuple[int, int] | None = None,
) -> DatasetCandidate | None:
    extension = _extension(link.url)
    if extension not in MEDIA_TYPES:
        return None
    year_month = _roc_month(f"{link.label} {link.url}") or inherited_month
    if year_month is None:
        return None
    return DatasetCandidate(
        dataset=dataset,
        region=region,
        roc_year=year_month[0],
        month=year_month[1],
        source_page_url=source_page_url,
        download_url=link.url,
        original_filename=_filename(link.url),
        extension=extension,
        media_type=MEDIA_TYPES[extension],
        supported_for_parse=extension in {".csv", ".xlsx"},
    )


def _deduplicate(
    candidates: Iterable[DatasetCandidate],
) -> tuple[DatasetCandidate, ...]:
    by_key = {
        (
            item.dataset,
            item.region,
            item.roc_year,
            item.month,
            item.download_url,
        ): item
        for item in candidates
    }
    return tuple(
        sorted(
            by_key.values(),
            key=lambda item: (
                item.roc_year,
                item.month,
                item.dataset,
                item.download_url,
            ),
        )
    )


def current_candidate(
    candidates: Iterable[DatasetCandidate],
    dataset: str,
) -> DatasetCandidate:
    supported = [
        item
        for item in candidates
        if item.dataset == dataset and item.supported_for_parse
    ]
    if not supported:
        raise SourceDiscoveryError(f"{dataset} 找不到機器可讀的 CSV 或 XLSX")
    return max(
        supported,
        key=lambda item: (item.roc_year, item.month, item.download_url),
    )


class HsinchuCountySource:
    INDEX = "https://civil.hsinchu.gov.tw/cl.aspx?n=1224"
    REGION = "新竹縣"

    def __init__(self, http):
        self.http = http

    def discover_available(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
    ) -> tuple[DatasetCandidate, ...]:
        categories: list[tuple[str, str]] = []
        for link in _links(self.http, self.INDEX):
            label = link.label
            if "年齡" in label:
                categories.append((link.url, "age_population"))
            elif "戶籍動態" in label or "動態登記" in label:
                categories.append((link.url, "migration"))
        candidates: list[DatasetCandidate] = []
        for page_url, dataset in categories:
            for link in _links(self.http, page_url):
                year_month = _roc_month(f"{link.label} {link.url}")
                if year_month is None or not _in_range(year_month, start, end):
                    continue
                item = _candidate(
                    link=link,
                    source_page_url=self.INDEX,
                    dataset=dataset,
                    region=self.REGION,
                )
                if item is not None:
                    candidates.append(item)
                    continue
                try:
                    detail_links = _links(self.http, link.url)
                except HTTPError:
                    continue
                for attachment in detail_links:
                    item = _candidate(
                        link=attachment,
                        source_page_url=self.INDEX,
                        dataset=dataset,
                        region=self.REGION,
                        inherited_month=year_month,
                    )
                    if item is not None:
                        candidates.append(item)
        return _deduplicate(candidates)


class HsinchuCitySource:
    AGE_INDEX = (
        "https://dep-civil.hccg.gov.tw/ch/home.jsp?id=58&parentpath=0,4,46"
    )
    MIGRATION_INDEX = (
        "https://dep-civil.hccg.gov.tw/ch/home.jsp?id=56&parentpath=0,4,46"
    )
    REGION = "新竹市"

    def __init__(self, http):
        self.http = http

    def _discover_dataset(
        self,
        index_url: str,
        dataset: str,
        start: tuple[int, int],
        end: tuple[int, int],
    ) -> list[DatasetCandidate]:
        candidates: list[DatasetCandidate] = []
        for link in _links(self.http, index_url):
            year_month = _roc_month(f"{link.label} {link.url}")
            if year_month is None or not _in_range(year_month, start, end):
                continue
            direct = _candidate(
                link=link,
                source_page_url=index_url,
                dataset=dataset,
                region=self.REGION,
            )
            if direct is not None:
                candidates.append(direct)
                continue
            try:
                detail_links = _links(self.http, link.url)
            except HTTPError:
                continue
            for attachment in detail_links:
                item = _candidate(
                    link=attachment,
                    source_page_url=index_url,
                    dataset=dataset,
                    region=self.REGION,
                    inherited_month=year_month,
                )
                if item is not None:
                    candidates.append(item)
        return candidates

    def discover_available(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
    ) -> tuple[DatasetCandidate, ...]:
        return _deduplicate(
            self._discover_dataset(
                self.AGE_INDEX, "age_population", start, end
            )
            + self._discover_dataset(
                self.MIGRATION_INDEX, "migration", start, end
            )
        )


class MiaoliCountySource:
    INDEX_TEMPLATE = "https://mlhr.miaoli.gov.tw/xlsgetfile_{roc_year}.php"
    REGION = "苗栗縣"

    def __init__(self, http):
        self.http = http

    @staticmethod
    def _dataset_for_link(link: Link) -> str | None:
        label = f"{link.label} {link.url}"
        filename = Path(urlparse(link.url).path).name.lower()
        if "年齡分布統計" in label or filename.endswith("03.xlsx"):
            return "age_population"
        if (
            "詳細資料" in label
            or "戶籍動態" in label
            or filename.endswith("01.xlsx")
        ):
            return "migration"
        return None

    def discover_available(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
    ) -> tuple[DatasetCandidate, ...]:
        candidates: list[DatasetCandidate] = []
        for year in range(start[0], end[0] + 1):
            page_url = self.INDEX_TEMPLATE.format(roc_year=year)
            try:
                page_links = _links(self.http, page_url)
            except HTTPError as exc:
                if exc.code == 404:
                    exc.close()
                    continue
                raise
            for link in page_links:
                dataset = self._dataset_for_link(link)
                if dataset is not None:
                    item = _candidate(
                        link=link,
                        source_page_url=page_url,
                        dataset=dataset,
                        region=self.REGION,
                    )
                    if item is not None and _in_range(
                        (item.roc_year, item.month), start, end
                    ):
                        candidates.append(item)
                        continue
                year_month = _roc_month(f"{link.label} {link.url}")
                if (
                    year_month is None
                    or not _in_range(year_month, start, end)
                    or not re.search(
                        r"xlsgetfile_\d{5}m\.php$", urlparse(link.url).path
                    )
                ):
                    continue
                for attachment in _links(self.http, link.url):
                    dataset = self._dataset_for_link(attachment)
                    if dataset is None:
                        continue
                    item = _candidate(
                        link=attachment,
                        source_page_url=link.url,
                        dataset=dataset,
                        region=self.REGION,
                        inherited_month=year_month,
                    )
                    if item is not None:
                        candidates.append(item)
        return _deduplicate(candidates)
