# 已下載附件 URL 去重設計

日期：2026-07-30

## 目標

每月 `update` 仍解析三個官方索引頁以發現新附件，但只對 SQLite 尚未
記錄的完整 `download_url` 發出附件下載請求。已典藏附件不得因為它是
current candidate 而重抓。

## 判定與資料流程

1. Source adapter 照常列舉回補範圍內所有候選附件。
2. Repository 以完整 `download_url` 查詢既有 artifact。
3. URL 尚未出現：下載 response bytes、保存 raw、解析並寫入 SQLite。
4. URL 已有 `success` 或 `unsupported_media` artifact：不下載、不重解析。
5. URL 只有 `failed` artifact：從既有 `archive_path` 讀取不可變 raw bytes
   並重新解析，不新增 fetch event，也不連網抓附件。
6. Current candidate 若已有成功 artifact，直接通過採用檢查；若本機重解析
   仍失敗，維持 fail-closed，不發布新 exports。

索引頁請求不在附件去重範圍內，因為每月仍需靠索引頁發現新 URL。

## 明確取捨

- 同月份若官方新增不同 URL 的 CSV、XLSX 或 PDF，仍會下載。
- 官方若以完全相同 URL 覆蓋 bytes，程式不會自動偵測；這是避免重抓的
  明確代價。
- 去重不以「地區＋資料集＋年月」判定，避免既有 PDF 阻擋後來新增的
  可解析附件。

## 驗證

- 第二次執行相同 inventory 時，附件 HTTP call 與 fetch event 都不增加。
- 先保存有效 bytes、模擬 parser 失敗後，第二次可從 raw 重解析成功，
  且不增加 HTTP call。
- 新 URL 第一次仍會下載。
- Current candidate 的下載或本機解析失敗仍不得發布。
