# 週末去哪

全台展覽、市集、百貨優惠與節慶活動，依縣市與鄉鎮分類，每天早上自動更新。

## 檔案

| 檔案 | 用途 |
|---|---|
| `index.html` | 網站本體。讀同一層的 `events.json` |
| `events.json` | 人工查證的活動。每天 06:30（台北時間）由 Claude 排程更新後推送 |
| `crawled.json` | 爬蟲自動收錄的活動。每天 05:30 由 GitHub Actions 更新 |
| `overrides.json` | 爬蟲修正檔 |
| `netlify.toml` | Netlify 設定：不需建置，`events.json` 不快取 |
| `scripts/export_events.py` | 把網站資料庫的匯出檔合併成 `events.json`，並排除過期活動 |
| `scripts/build_index.py` | 把 Claude Artifact 版網頁包成完整的 `index.html` |

## 資料怎麼來

資料有兩個來源：爬蟲每天自動收錄的開放資料與公開活動頁（`crawled.json`，網頁上標「自動收錄」），以及 Claude 每天上網查證、處理投稿後整理的活動（`events.json`）。每筆活動都附來源連結，出發前請以主辦單位公告為準。

## 爬蟲

`scripts/crawl.py` 每天由 GitHub Actions（`.github/workflows/crawl.yml`）在台北時間 05:30 執行，來源是交通部觀光署與文化部的開放資料，以及 Accupass 公開活動頁。遵守 robots.txt、每個網站請求間隔 2 秒，被拒絕就停用該來源。結果寫進 `crawled.json`，網頁會和 `events.json`（人工查證）合併顯示，同一活動以人工查證的為準。`overrides.json` 是修正檔（補鄉鎮、改分類、隱藏不適合的活動）。
