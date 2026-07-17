# SAR 暗船切片取證工具 — 本機使用指引

`src/fetch_sar_chip.py` 的完整操作手冊：在報告頁看到可疑的殘餘暗船時，
從 Copernicus Data Space (CDSE) 把該偵測點周圍的 Sentinel-1 雷達影像切片
抓下來人工確認 — **只讀取偵測點附近幾 MB 的視窗，不會下載整景 1GB 影像**。

回答最常見的問題：**不需要 clone 整個專案。** 這支工具是單一獨立檔案，
不依賴專案內任何其他模組，下載一個 `.py` 檔就能跑。

---

## 1. 前置需求

| 項目 | 說明 |
|------|------|
| Python | 3.9 以上（建議 3.11） |
| pip 套件 | `requests` `boto3` `rasterio` `numpy` `matplotlib` |
| CDSE 帳號 | https://dataspace.copernicus.eu 免費註冊 |
| CDSE S3 金鑰 | 見下方步驟 3 |
| 網路 | 能連 `catalogue.dataspace.copernicus.eu` 與 `eodata.dataspace.copernicus.eu` |

## 2. 取得工具（二選一）

**方式 A — 只抓一個檔案（推薦）：**

```bash
curl -O https://raw.githubusercontent.com/s0914712/taiwan-grayzone-monitor/main/src/fetch_sar_chip.py
```

（或到 GitHub 網頁開 `src/fetch_sar_chip.py` → Raw → 另存新檔）

**方式 B — clone 整個專案**（想順便跑其他管線腳本才需要）：

```bash
git clone https://github.com/s0914712/taiwan-grayzone-monitor.git
cd taiwan-grayzone-monitor
```

## 3. 安裝套件

```bash
pip install requests boto3 rasterio numpy matplotlib
```

- **Windows**：`rasterio` 的官方 pip wheel 通常可直接安裝；若失敗，改用
  conda：`conda install -c conda-forge rasterio`，其餘照常 pip。
- **macOS / Linux**：pip 直接裝即可。
- 建議用虛擬環境（`python3 -m venv venv && source venv/bin/activate`），非必須。

## 4. 產生 CDSE S3 金鑰（一次性）

1. 開 https://eodata-s3keysmanager.dataspace.copernicus.eu
2. 用你的 CDSE 帳號登入
3. 「Generate credentials」→ 記下 **Access Key** 與 **Secret Key**
   （Secret 只顯示一次；遺失就刪掉重生一組）

## 5. 設定環境變數

**macOS / Linux：**

```bash
export CDSE_ACCESS_KEY='你的access key'
export CDSE_SECRET_KEY='你的secret key'
```

**Windows PowerShell：**

```powershell
$env:CDSE_ACCESS_KEY = '你的access key'
$env:CDSE_SECRET_KEY = '你的secret key'
```

**Windows CMD：**

```cmd
set CDSE_ACCESS_KEY=你的access key
set CDSE_SECRET_KEY=你的secret key
```

只在當前終端機生效；想永久保存可寫進 `~/.bashrc` / `~/.zshrc` 或系統環境變數。
**不要**把金鑰 commit 進任何 repo。

## 6. 從報告頁挑目標

1. 開 [暗船偵測地圖](https://s0914712.github.io/taiwan-grayzone-monitor/dark-vessels.html)
   或 [SAR×AIS 比對報告](https://s0914712.github.io/taiwan-grayzone-monitor/sar-ais-match.html)
2. 點一個 **紅色實心點**（殘餘暗船 — 本地 AIS 也無法解釋的目標）
3. 從 popup 抄下三個值：**日期**、**緯度**、**經度**
   （popup 也會顯示海域法域，例如「鄰接區（24浬內）」— 判讀時有用）

> 空心橘圈（覆蓋外未驗證）也可以查 — 它們只是超出本地 AIS 保留期而
> 無法交叉比對，SAR 影像本身照樣查得到。

## 7. 執行

```bash
python3 fetch_sar_chip.py <緯度> <經度> <日期> [--time HH:MM] [--size-km 8] [-o 輸出.png]
```

實際例子（2026-06-26 在金門外海 24.46N, 118.59E 的偵測）：

```bash
python3 fetch_sar_chip.py 24.46 118.59 2026-06-26
```

台灣周邊一天最多兩次過境（升軌 ≈09:50 UTC、降軌 ≈21:55 UTC）。
同一天兩次都有產品時，用 `--time` 指定要哪一次：

```bash
python3 fetch_sar_chip.py 24.46 118.59 2026-06-26 --time 21:55   # 降軌（晚間）
python3 fetch_sar_chip.py 24.46 118.59 2026-06-26 --time 09:50   # 升軌（上午）
```

其他參數：
- `--size-km 12` — 切片邊長改 12 km（預設 8；越大讀越慢）
- `-o my_chip.png` — 指定輸出檔名（預設 `sar_chip_<日期>_<緯度>_<經度>.png`）

## 8. 輸出解讀

**終端機輸出：**

```
🔎 查詢 2026-06-26 涵蓋 (24.46, 118.59) 的 IW GRDH 產品...
   ✅ S1A_IW_GRDH_1SDV_20260626T215402_...
      成像: 2026-06-26T21:54:02.000Z          ← SAR 實際成像時刻（UTC）
📡 S3 視窗讀取: s1a-iw-grd-vv-....tiff
🎯 亮目標: ~85.0 m、峰值 23.4× 海面背景（41 px）
🖼  已存: sar_chip_2026-06-26_24.460_118.590.png
```

**PNG 標註：**
- 灰階底圖 = 雷達回波強度（dB），海面暗、金屬船體亮
- **青色十字** = 偵測回報的座標
- **紅色圓圈** = 工具找到的亮目標位置
- 左下 **1 km 比例尺**

**判讀：**

| 看到什麼 | 意義 |
|----------|------|
| 十字附近有明顯亮點（峰值 >10× 背景） | ✅ 真實雷達目標，暗船偵測可信 |
| `~XX m` 長度估計 | 粗估目標長度（10m 像素，±20-30m）；可與嫌疑船的 AIS 登記船長比對 — 差太多是身分冒用線索 |
| 十字附近一片均勻暗 | ⚪ 可能是雜訊誤報、或目標太小/木殼（低雷達截面） |
| 亮區巨大且 `saturated` 警告 | ⚠️ 陸地、島礁或大型固定結構，長度不可信 |
| 亮點固定出現在同位置（換日期重查） | 🏗 固定設施（風機/平台）— 不是船 |

## 9. 常見問題

**「該日期查無涵蓋此點的產品」**
當天 Sentinel-1 沒掃到這裡（衛星 12 天重訪、雙星約 6 天，台灣附近不是每天有）。
偵測既然存在，通常表示日期抄錯或座標抄反（緯度在前、經度在後）。
可對照 repo 的 `data/s1_pass_times.json` 看該日期有沒有過境。

**403 / Access Denied**
金鑰打錯或已被刪除 — 回步驟 4 重生一組。注意環境變數名稱是
`CDSE_ACCESS_KEY` / `CDSE_SECRET_KEY`。

**rasterio 裝不起來**
用 conda：`conda install -c conda-forge rasterio boto3 numpy matplotlib requests`。

**讀取很慢**
S3 range read 一般 10-60 秒（跨洲連線）。`--size-km` 越大越慢；8 km 通常夠用。

**CDSE 有流量限制嗎？**
有 quota，但本工具每次只做一次目錄查詢＋一個視窗讀取（數 MB、數十個
request），個案取證用量遠低於限制。

## 10. 安全提醒

- S3 金鑰等同你的 CDSE 帳號資料存取權 — 只放在本機環境變數，
  不要寫進程式碼、不要 commit、不要貼到 issue/聊天。
- 疑似外洩時到 keys manager 刪除舊金鑰重生即可。
