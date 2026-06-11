# 改進計畫 — Taiwan Gray Zone Monitor

> 2026-06-11 程式碼健檢後擬定的四面向改進計畫：管線穩定性、安全性清理、前端效能/UX、程式碼品質。

## 背景與問題

健檢發現的核心問題：

- **資料完整性風險**：所有 JSON 寫入皆非 atomic（`json.dump` 直接寫目標檔），CI 中斷會 commit 壞檔；外部 API（GFW、ITU、MODA、SCFI）呼叫無 retry/backoff
- **靜默失敗**：大量 `except Exception: pass/continue` 吞掉錯誤；workflow 每步 `continue-on-error: true`，失敗無聲無息地沿用舊資料
- **安全性**：`src/fetch_ais_data.py` 內嵌 5 組 SOCKS5 proxy 明文帳密作為 fallback（亦存在於 git 歷史）
- **程式碼重複**：`haversine_km()` 在 3 個檔案各自實作（經驗證數學上完全相同）
- **前端缺口**：無資料新鮮度提示、MMSI 搜尋無 debounce、全站零 `aria-label`

## Commit 1 — 共用模組（基礎建設）

### 新檔 `src/geo_utils.py`
- `haversine_km()` — 統一三處重複實作（`analyze_suspicious.py`、`detect_ship_transfers.py`、`publish_threads.py`）
- `calc_bearing()` — 自 `analyze_suspicious.py` 移入
- `publish_threads.py` 以 `import ... as _haversine_km` 別名引入，不動既有呼叫點

### 新檔 `src/io_utils.py`（僅 stdlib + requests；不可 import pandas/scipy，因 update-ais.yml 只安裝 requests+pysocks）
1. `atomic_write_json(path, obj, *, indent=2, compact=False)` — temp 檔寫在目標同目錄，fsync 後 `os.replace`
2. `load_json(path, default, label=None, expect_type=None)` — 失敗印警告並回傳 default，取代靜默 except
3. `make_retry_session(...)` — requests Session + urllib3 Retry（429/5xx，指數退避）。`publish_threads.py` 的發文 POST 非冪等，禁止採用

## Commit 2 — 管線穩定性

1. **Atomic 寫入**：替換主要管線輸出點（fetch_ais_data、generate_dashboard、analyze_suspicious、detect_ship_transfers、fetch_gfw_data、fetch_weekly_dark_vessels、exercise_prediction、extract_all_routes 及次要腳本）
2. **Retry 採用**：GFW、ITU MARS、MODA 電纜、SCFI、軍演資料等 HTTP 呼叫。不動 `fetch_ais_data.py` 的 proxy 輪換迴圈（已有自己的 retry 語意）
3. **靜默 except 改為有訊息**：load-with-default 一律改 `load_json()`；排除規則失敗每規則印一次；route 檔解析失敗印總數
4. **新檔 `src/validate_outputs.py`**：檢查關鍵 JSON 存在/可解析/非空（docs/data.json、data/ais_snapshot.json vessels>0、軌跡檔、suspicious_vessels），`--restore` 對失敗的 tracked 檔 `git checkout --` 還原上次好版本，任一必要檢查失敗 exit 1
5. **Workflow 接線**（update-data.yml + update-ais.yml）：管線步驟 → cache save → Validate（continue-on-error + id）→ Commit（移除 inline VESSEL_COUNT 守衛，由 validate --restore 接手）→ 驗證失敗則整個 job 失敗（響亮訊號）

## Commit 3 — 安全性清理

1. 刪除 `get_proxy_list()` 內嵌帳密 fallback；POOL/PROXY_LIST 未設時印清楚訊息並回傳空清單（保留「沿用舊快照」行為）
2. 刪除無引用的殘留檔 `src/local_fetch_and_push (1).py`
3. ⚠️ **需人工處理**：帳密已存在 git 歷史，需至 pingproxies 供應商端輪換；歷史清除（git filter-repo）另議

## Commit 4 — 前端快贏

1. **MMSI 搜尋 debounce**（map.js）：input 事件 600ms debounce，滿 9 位數字自動查詢；Enter/按鈕仍處理 5-8 位
2. **資料新鮮度提示**（app.js + i18n.js + main.css）：「更新時間 (X 分鐘前)」相對時間、>4h 顯示 stale 警告（橘色）、每分鐘重繪、支援 zh/en
3. **ARIA 無障礙**：語言切換、搜尋框、查詢按鈕、bottom-sheet handle 加 `aria-label`；船舶 SVG 圖示加 `role="img" aria-label`

## 驗證方式

1. `python3 -m py_compile src/*.py`
2. 無 token 本地管線：analyze_suspicious → detect_ship_transfers → generate_dashboard → validate_outputs 全部 exit 0
3. Proxy 守衛：POOL 未設時印訊息、exit 0、不動資料檔
4. Validation 負面測試：壞檔 → exit 1；`--restore` → 還原且 exit 1
5. 前端：`node --check` + 本地 http.server 目視檢查
6. Workflow YAML 可解析

## 未來工作

**已完成（第二輪）：**
- ✅ map.js 第一階段拆分：~430 行靜態資料表與純函式抽至 `docs/js/map-data.js`（MapData 模組），map.js 1,967 → 1,537 行；service worker 快取 bump 至 v2 並預快取新檔
- ✅ pytest 測試套件（geo_utils / io_utils / validate_outputs / 排除規則，27 項）+ node 前端煙霧測試 + `.github/workflows/tests.yml` CI

**待辦：**
- map.js 第二階段拆分（vessel-renderer / route-loader / layer-manager）— 涉及共享可變狀態（map、layers、caches），需瀏覽器實測，建議搭配本地開發環境進行
- 兩個動畫頁（ais-animation 3,193 行、cn-fishing-animation 1,837 行）inline JS 模組化 — 同樣需瀏覽器實測
- **git 歷史帳密清除**（filter-repo + force push）— 需使用者決策：force push 會影響所有 clone，且 CI 自動 commit 流程需暫停配合；無論是否清除，**pingproxies 憑證都必須先在供應商端輪換**
- **repo 膨脹**（pack 216MB；`docs/vessel_routes/` 23,012 檔 129MB）— 需架構決策：選項包括 (a) 軌跡檔搬到獨立資料分支、(b) GitHub Actions artifacts + 前端改 API 載入、(c) 合併為依 MMSI 前綴分片的大檔。各選項都會改變前端載入路徑
- `data.json`（3.6MB）按主題分割 / 分頁載入 — 改善首屏載入，需同步調整各頁面 fetch 邏輯
