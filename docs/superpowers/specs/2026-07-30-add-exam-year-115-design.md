# 新增民國 115 年會考人口推估設計

日期：2026-07-30

## 目標

將預設會考戶籍人口 cohort 推估範圍由民國 116–130 年擴充為
115–130 年，並同步更新 CSV export、README 圖表與再生工具。115 年
沿用既有單歲戶籍人口按出生月份重疊比例加權的方法，不混入實際畢業
或報考人數。

## 115 年基準結果

以三地最新成功解析的民國 `115-06` 單歲人口 snapshot 計算
民國 99 年 9 月至 100 年 8 月出生 cohort：

| 地區 | 推估人數 | 資料年月 |
| --- | ---: | --- |
| 新竹縣 | 5,951 | 115-06 |
| 新竹市 | 4,666 | 115-06 |
| 苗栗縣 | 4,287 | 115-06 |
| 三區合計 | 14,904 | 115-06 |

所有出生月份都早於 snapshot 參考月，不需補未來月份，因此資料完整性
必須為 `完整 cohort`。

## 預設範圍與資料流

- `update`、`analyze`、service API 與 CLI 的預設 `start_year` 改為 115；
  `end_year` 維持 130。
- `exam_population_rows()` 保持通用的 start/end 介面與既有估算公式，
  不加入 115 年特例或硬編碼結果。
- 使用既有 SQLite 離線執行 `analyze`，建立新的 timestamp export
  `exam_population_115_130.csv`。
- `data/population/exports/` 中既有目錄及其所有 bytes 均保持不變；
  新結果只新增一個 export 目錄。
- 未執行人口下載，也不改動 raw 或 SQLite。

## 圖表與 README

- 圖表 loader 預設尋找最新的
  `exports/*/exam_population_115_130.csv`。
- loader 嚴格要求 16 列、年度連續且恰為 115–130、三地加總正確，
  並要求只有 130 年為暫估。
- 每個系列新增 115 年資料點；130 年仍使用空心點及暫估註記。
- 新圖輸出為 `docs/images/exam-population-115-130.svg`。
- 目前版本移除 `docs/images/exam-population-116-130.svg`；舊圖仍可從
  Git 歷史還原。
- README 的範圍說明、圖片連結、CSV 連結及重產指令同步改為 115–130。

## 失敗與相容性

- 找不到新的 `exam_population_115_130.csv` 時，產圖程式明確失敗，
  不回退使用舊範圍，以免 README 圖與文字不一致。
- CSV 若缺少 115、年度不連續、115 數值不等於三地加總，或 115 被標為
  暫估，loader 必須拒絕。
- 使用者仍可明確傳入其他 `start_year`；本次只改預設值，不縮減 API
  可用範圍。
- 舊的 `exam_population_116_130.csv` 保留且仍可被直接讀取分析，但不再
  作為 README 圖表的預設來源。

## 測試與驗證

- estimator／analysis 測試確認 115 年出生區間、三地與合計計算。
- service／CLI 測試確認未指定範圍時輸出 `exam_population_115_130.csv`。
- chart 測試改為 16 年、每系列 16 點，並驗證 115 與 130 的完整性標記。
- 使用 repository 的真實 SQLite 離線產生 export，驗證 115 年精確值為
  `5,951 / 4,666 / 4,287 / 14,904`。
- 重產 SVG 後執行 XML 驗證及實際視覺檢查。
- 執行完整 unittest、compileall、artifact validator、immutable 舊資料
  hash 比對與 `git diff --check`。

## GitHub 交付

spec、實作計畫、程式、測試、新 export、SVG 與 README 會逐步提交並
推送至 `feature/add-exam-year-115`。完整驗證後 fast-forward 合併並推送
`main`，本機 feature branch再刪除，遠端 feature branch保留作 checkpoint。
