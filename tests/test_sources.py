from urllib.error import HTTPError
import ssl
import unittest

from exam_population.sources import (
    HsinchuCitySource,
    HsinchuCountySource,
    HttpClient,
    MiaoliCountySource,
    SourceDiscoveryError,
    current_candidate,
)


COUNTY_INDEX = """
<a href="News.aspx?n=1331&amp;sms=9528">各鄉鎮市村里鄰戶數、人口數與戶籍動態登記數按性別、登記項目及區域分</a>
<a href="News.aspx?n=1333&amp;sms=9530">各鄉鎮市村里現住人口數按性別及年齡分</a>
"""
COUNTY_AGE = """
<a href="/files/11506-age.xlsx">115年6月各鄉鎮市村里現住人口數按性別及年齡分</a>
<a href="/files/11401-age.xlsx">114年1月各鄉鎮市村里現住人口數按性別及年齡分</a>
"""
COUNTY_MIGRATION = """
<a href="/files/11506-dynamic.xlsx">115年6月戶籍動態登記數</a>
<a href="/files/11401-dynamic.xlsx">114年1月戶籍動態登記數</a>
"""
COUNTY_ASHX_AGE = """
<a href="https://ws.hsinchu.gov.tw/Download.ashx?u=ignored&amp;n=cmVwb3J0Lnhsc3g%3D">
115年6月 [開啟彈窗]115年6月
</a>
"""
COUNTY_DETAIL_AGE = """
<a href="News_Content.aspx?n=1333&amp;s=280343">115年2月</a>
"""
COUNTY_DETAIL_ATTACHMENT = """
<a href="https://ws.hsinchu.gov.tw/Download.ashx?u=ignored&amp;n=cmVwb3J0Lnhsc3g%3D">
附件下載
</a>
"""
CITY_AGE = """
<a href="/detail/age-11506">115年6月現住人口性別及年齡統計表</a>
<a href="/files/11504-age.csv">115年4月現住人口性別及年齡統計表 CSV</a>
"""
CITY_AGE_DETAIL = '<a href="/files/11506-age.pdf">下載PDF</a>'
CITY_QUERY_ATTACHMENT = """
<a href="/uploaddowndoc?file=Demographics%2F202605011012500.csv&amp;filedisplay=11504%E6%9C%88%E7%B5%B1%E8%A8%88%E5%A0%B1%E8%A1%A83.csv&amp;flag=doc">
115年4月底現住人口性別及年齡統計表
</a>
"""
CITY_MIGRATION = """
<a href="/files/11506-migration.csv">115年6月各區動態登記數</a>
<a href="/files/11401-migration.csv">114年1月各區動態登記數</a>
"""
MIAOLI_115 = """
<a href="/xls/115/1151203.xlsx">年齡分布統計</a>
<a href="/xls/115/1151201.xlsx">戶數、人口數詳細資料表</a>
"""
MIAOLI_116 = """
<a href="/xls/116/1160103.xlsx">年齡分布統計</a>
<a href="/xls/116/1160101.xlsx">戶數、人口數詳細資料表</a>
"""
MIAOLI_115_MONTH_INDEX = """
<a href="/xlsgetfile_11506m.php">115年六月份人口統計下載區</a>
"""
MIAOLI_115_MONTH = """
<a href="/xls/115/1150601.xlsx">戶數、人口數詳細資料表</a>
<a href="/xls/115/1150603.xlsx">年齡分布統計</a>
<a href="/xls/115/1150604.xlsx">現住原住民人口數按性別及年齡分</a>
<a href="/xls/115/1150611.xlsx">相同性別結婚人數統計</a>
"""


class FakeHttp:
    def __init__(self, pages):
        self.pages = pages
        self.requested_urls = []

    def get(self, url):
        self.requested_urls.append(url)
        if url not in self.pages:
            raise HTTPError(url, 404, "not found", {}, None)
        return self.pages[url].encode()


class SourceTests(unittest.TestCase):
    def test_http_client_keeps_tls_verification_without_x509_strict_mode(self):
        context = HttpClient().ssl_context
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertFalse(context.verify_flags & ssl.VERIFY_X509_STRICT)

    def test_hsinchu_county_enumerates_two_datasets_over_range(self):
        pages = {
            HsinchuCountySource.INDEX: COUNTY_INDEX,
            "https://civil.hsinchu.gov.tw/News.aspx?n=1331&sms=9528": COUNTY_MIGRATION,
            "https://civil.hsinchu.gov.tw/News.aspx?n=1333&sms=9530": COUNTY_AGE,
        }
        found = HsinchuCountySource(FakeHttp(pages)).discover_available(
            (114, 1), (115, 6)
        )
        keys = {(item.dataset, item.roc_year, item.month) for item in found}
        self.assertIn(("age_population", 114, 1), keys)
        self.assertIn(("migration", 115, 6), keys)

    def test_hsinchu_county_reads_xlsx_extension_from_download_query(self):
        pages = {
            HsinchuCountySource.INDEX: COUNTY_INDEX,
            "https://civil.hsinchu.gov.tw/News.aspx?n=1331&sms=9528": "",
            "https://civil.hsinchu.gov.tw/News.aspx?n=1333&sms=9530": COUNTY_ASHX_AGE,
        }
        found = HsinchuCountySource(FakeHttp(pages)).discover_available(
            (115, 6), (115, 6)
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].extension, ".xlsx")
        self.assertEqual(found[0].original_filename, "report.xlsx")
        self.assertTrue(found[0].supported_for_parse)

    def test_hsinchu_county_follows_month_detail_page_to_attachment(self):
        pages = {
            HsinchuCountySource.INDEX: COUNTY_INDEX,
            "https://civil.hsinchu.gov.tw/News.aspx?n=1331&sms=9528": "",
            "https://civil.hsinchu.gov.tw/News.aspx?n=1333&sms=9530": COUNTY_DETAIL_AGE,
            "https://civil.hsinchu.gov.tw/News_Content.aspx?n=1333&s=280343": (
                COUNTY_DETAIL_ATTACHMENT
            ),
        }
        found = HsinchuCountySource(FakeHttp(pages)).discover_available(
            (115, 2), (115, 2)
        )
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0].roc_year, found[0].month), (115, 2))
        self.assertEqual(found[0].extension, ".xlsx")

    def test_hsinchu_city_archives_pdf_but_current_uses_csv(self):
        pages = {
            HsinchuCitySource.AGE_INDEX: CITY_AGE,
            HsinchuCitySource.MIGRATION_INDEX: CITY_MIGRATION,
            "https://dep-civil.hccg.gov.tw/detail/age-11506": CITY_AGE_DETAIL,
        }
        found = HsinchuCitySource(FakeHttp(pages)).discover_available(
            (114, 1), (115, 6)
        )
        pdf = next(item for item in found if item.extension == ".pdf")
        self.assertFalse(pdf.supported_for_parse)
        self.assertEqual(current_candidate(found, "age_population").month, 4)
        self.assertIn("id=58", current_candidate(found, "age_population").source_page_url)
        self.assertIn("id=56", current_candidate(found, "migration").source_page_url)

    def test_hsinchu_city_reads_csv_extension_from_download_query(self):
        pages = {
            HsinchuCitySource.AGE_INDEX: CITY_QUERY_ATTACHMENT,
            HsinchuCitySource.MIGRATION_INDEX: "",
        }
        found = HsinchuCitySource(FakeHttp(pages)).discover_available(
            (115, 4), (115, 4)
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].extension, ".csv")
        self.assertEqual(found[0].original_filename, "11504月統計報表3.csv")
        self.assertTrue(found[0].supported_for_parse)

    def test_miaoli_uses_dynamic_year_pages(self):
        pages = {
            MiaoliCountySource.INDEX_TEMPLATE.format(roc_year=115): MIAOLI_115,
            MiaoliCountySource.INDEX_TEMPLATE.format(roc_year=116): MIAOLI_116,
        }
        http = FakeHttp(pages)
        found = MiaoliCountySource(http).discover_available((115, 12), (116, 1))
        self.assertEqual({item.roc_year for item in found}, {115, 116})
        self.assertIn(
            MiaoliCountySource.INDEX_TEMPLATE.format(roc_year=116),
            http.requested_urls,
        )

    def test_miaoli_follows_year_page_to_month_page_attachments(self):
        month_url = "https://mlhr.miaoli.gov.tw/xlsgetfile_11506m.php"
        pages = {
            MiaoliCountySource.INDEX_TEMPLATE.format(
                roc_year=115
            ): MIAOLI_115_MONTH_INDEX,
            month_url: MIAOLI_115_MONTH,
        }
        found = MiaoliCountySource(FakeHttp(pages)).discover_available(
            (115, 6), (115, 6)
        )
        self.assertEqual(
            {(item.dataset, item.roc_year, item.month) for item in found},
            {
                ("age_population", 115, 6),
                ("migration", 115, 6),
            },
        )
        self.assertEqual(
            {item.original_filename for item in found},
            {"1150601.xlsx", "1150603.xlsx"},
        )

    def test_missing_new_miaoli_year_keeps_previous_candidates(self):
        pages = {
            MiaoliCountySource.INDEX_TEMPLATE.format(roc_year=115): MIAOLI_115,
        }
        found = MiaoliCountySource(FakeHttp(pages)).discover_available(
            (115, 12), (116, 1)
        )
        self.assertTrue(found)
        self.assertEqual({item.roc_year for item in found}, {115})

    def test_no_supported_candidate_is_explicit_failure(self):
        pages = {
            HsinchuCitySource.AGE_INDEX: CITY_AGE,
            HsinchuCitySource.MIGRATION_INDEX: "",
            "https://dep-civil.hccg.gov.tw/detail/age-11506": CITY_AGE_DETAIL,
        }
        found = HsinchuCitySource(FakeHttp(pages)).discover_available(
            (115, 5), (115, 6)
        )
        with self.assertRaisesRegex(SourceDiscoveryError, "機器可讀"):
            current_candidate(found, "age_population")


if __name__ == "__main__":
    unittest.main()
