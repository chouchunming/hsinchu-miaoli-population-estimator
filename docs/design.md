# 設計摘要

## 資料與典藏

三個地區各有單歲人口與戶籍動態兩個資料集。來源 adapter 列舉民國
`114-01` 起的候選附件；CSV/XLSX 可解析，PDF 只保存。每份 response
bytes 以 SHA-256 保存於：

```text
data/population/raw/<region>/<dataset>/<民國年月>/<sha256>.<ext>
```

SQLite 保存 artifact metadata、取得事件、下載失敗、正規化單歲人口及
戶籍動態。相同年月若官方重新上傳不同 bytes，保留不同 hash 版本。

## 會考推估

民國 `E` 年會考 cohort 是 `E-16` 年 9 月至 `E-15` 年 8 月出生者。
單歲人口依它與出生月份區間的重疊月數乘以 `重疊月數 / 12`。尚未發布
的 cohort 尾端只可用最新 0 歲人口月平均暫估，輸出必須標記。

## 戶籍動態

年度 CSV 加總官方 `遷入人數_合計` 與 `遷出人數_合計`。這些是戶籍
登記事件總數，包含縣市內跨區移動；第一版不推導跨縣市移入人口。

## 年級 cohort

比較同一出生 cohort 在相同地區、相同月份、相隔 12 個月的人口。
期末月份 9–12 月屬同一民國學年度，1–8 月屬前一民國學年度。人口變化
混合遷入、遷出、死亡及戶籍更正，不稱為實際移入。

## 發布 gate

每個地區／資料集選擇年月最大的 CSV/XLSX 作 current candidate。六個
current candidate 全部成功才建立新 exports；歷史缺月或 PDF 產生
`partial` warning，但不把缺漏補零。
