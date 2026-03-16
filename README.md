# 🛰️ Taiwan Gray Zone Monitor | 台灣灰色地帶監測系統

<div align="center">

![Status](https://img.shields.io/badge/status-active-00f5ff?style=for-the-badge)
![License](https://img.shields.io/badge/license-MIT-blue?style=for-the-badge)
![Data](https://img.shields.io/badge/data-AIS%20%2B%20SAR%20%2B%20GFW-orange?style=for-the-badge)
![Auto Update](https://img.shields.io/badge/update-every%202hr-brightgreen?style=for-the-badge)

**即時整合 AIS、SAR 衛星與 GFW 資料的台灣周邊海域開源情報（OSINT）監測平台**

[🌐 Live Demo](https://s0914712.github.io/taiwan-grayzone-monitor/) | [📊 統計頁面](https://s0914712.github.io/taiwan-grayzone-monitor/statistics.html) | [🔦 暗船分析](https://s0914712.github.io/taiwan-grayzone-monitor/dark-vessels.html)

</div>

---

## 🔍 這個專案在做什麼？

中國對台灣的灰色地帶行動不僅限於軍機擾台，**海上活動**同樣是關鍵戰場。本系統透過多重公開資料源，自動監測台灣周邊海域的異常船隻行為：

| 威脅類型 | 說明 | 偵測方式 |
|----------|------|----------|
| 🔦 **暗船** | 關閉 AIS 的船隻，衛星仍可偵測 | GFW SAR 衛星影像分析 |
| 🚨 **可疑船隻** | 行為符合灰色地帶行動特徵 | CSIS 方法論自動判定 |
| 🔄 **AIS 身分變更** | 船隻更改名稱/MMSI/旗幟 | 歷史快照比對 |
| 🎣 **異常漁船聚集** | 中國漁船在敏感海域群聚 | 漁撈熱點分析 |
| 🔗 **海底電纜威脅** | 電纜斷裂/故障監測 | 電纜狀態追蹤 |

### 方法論來源

基於 CSIS（戰略與國際研究中心）報告 **[「Signals in the Swarm」](https://www.csis.org/analysis/signals-swarm-data-behind-chinas-maritime-gray-zone-campaign-near-taiwan)**：

> 可疑船隻判定標準：軍演區停留 > 30%、漁撈熱點停留 < 10%、AIS 異常行為

---

## 📱 功能特色

### 即時監測儀表板
- **今日概況看板** — 一眼掌握 AIS 船隻數、暗船、可疑船隻、身分變更
- **智慧縮放** — 全區域顯示分區聚合統計，拉近後顯示個別船隻
- **多圖層疊加** — AIS 船隻、暗船、漁撈熱點、海底電纜自由切換
- **船隻航跡追蹤** — 追蹤特定船隻歷史航跡（如 LONG AN 隆安號）

### 動畫回放
- **暗船週動畫** — 一週暗船活動熱點變化
- **AIS 船位動畫** — 即時船位動態回放
- **中國漁船動畫** — 大陸漁船活動範圍變化

### 分析工具
- **暗船深度分析** — 各區域暗船密度、趨勢圖表
- **統計儀表板** — 船型分布、旗幟國分析、歷史趨勢
- **身分追蹤** — 船隻 AIS 身分變更事件時間線
- **CSIS 風險評分** — 自動標記高風險船隻

### 自動報告 & 社群發布
- **每日/每週摘要** — 自動產生結構化報告
- **Threads 自動發布** — 每日 08:30 自動發文（含圖表）
- **分享按鈕** — 一鍵分享到 Twitter/X、LINE

---

## 🖥️ 系統架構

```
                    ┌──────────────────────────┐
                    │     GitHub Actions        │
                    │  (每 2 小時 / 每 12 小時)  │
                    └────────┬─────────────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
     ┌────────────┐  ┌────────────┐  ┌────────────┐
     │ AIS 即時   │  │ GFW SAR    │  │ 海纜狀態   │
     │ 船位資料   │  │ 暗船資料   │  │ 故障監測   │
     │ (港務局)   │  │ (衛星)     │  │            │
     └─────┬──────┘  └─────┬──────┘  └─────┬──────┘
           │               │               │
           ▼               ▼               ▼
     ┌─────────────────────────────────────────┐
     │         generate_dashboard.py            │
     │   analyze_suspicious.py (CSIS 方法論)    │
     │   exercise_prediction.py                 │
     └─────────────┬───────────────────────────┘
                   │
          ┌────────┴────────┐
          ▼                 ▼
   ┌─────────────┐  ┌──────────────┐
   │ GitHub Pages │  │ Threads 發布 │
   │ (前端儀表板) │  │ (每日摘要)   │
   └─────────────┘  └──────────────┘
```

### 技術棧

| 組件 | 技術 |
|------|------|
| 前端 | Vanilla HTML/CSS/JS + Leaflet.js + Chart.js |
| 地圖 | Leaflet.js 1.9.4 + CartoDB Dark Tiles |
| 資料管線 | Python 3.11 (pandas, requests, scipy) |
| 自動化 | GitHub Actions (3 workflows) |
| 部署 | GitHub Pages（零成本、零伺服器） |
| 社群發布 | Threads Graph API |

### 資料更新頻率

| 資料源 | 頻率 | 說明 |
|--------|------|------|
| AIS 即時船位 | 每 2 小時 | 台灣港務局 Open Data |
| GFW SAR 暗船 | 每 12 小時 | 衛星雷達影像分析 |
| CSIS 可疑分析 | 每 12 小時 | 自動套用方法論判定 |
| AIS 身分變更 | 每 2 小時 | 歷史快照差異比對 |
| Threads 報告 | 每日 08:30 | 自動摘要 + 圖表發布 |

---

## 🗂️ 專案結構

```
taiwan-grayzone-monitor/
├── docs/                          # GitHub Pages 前端
│   ├── index.html                 # 主監測頁（今日概況 + 地圖）
│   ├── dark-vessels.html          # 暗船深度分析
│   ├── statistics.html            # 統計儀表板
│   ├── identity-history.html      # AIS 身分變更追蹤
│   ├── intro.html                 # 專案介紹頁
│   ├── weekly-animation.html      # 暗船週動畫
│   ├── ais-animation.html         # AIS 船位動畫
│   ├── cn-fishing-animation.html  # 中國漁船動畫
│   ├── js/
│   │   ├── app.js                 # 應用控制器
│   │   ├── map.js                 # 地圖模組（Leaflet）
│   │   ├── charts.js              # 圖表模組（Chart.js）
│   │   └── mobile-nav.js          # 共用行動版導航
│   ├── css/main.css               # 全站樣式
│   ├── data.json                  # 彙整後的監測資料
│   └── vessel_routes/             # 個別船隻航跡資料
├── src/                           # 資料管線
│   ├── fetch_ais_data.py          # 擷取 AIS 即時船位
│   ├── fetch_gfw_data.py          # 擷取 GFW SAR 資料
│   ├── analyze_suspicious.py      # CSIS 可疑船隻分析
│   ├── generate_dashboard.py      # 產生前端資料
│   ├── extract_vessel_route.py    # 擷取個別船隻航跡
│   ├── exercise_prediction.py     # 軍演預測分析
│   ├── fetch_weekly_dark_vessels.py # 週暗船動畫資料
│   ├── generate_summary.py        # 每日/週摘要報告
│   └── publish_threads.py         # Threads 自動發布
└── .github/workflows/
    ├── update-ais.yml             # AIS 資料更新（每 2 小時）
    ├── update-data.yml            # 全量資料更新（每 12 小時）
    └── publish-threads.yml        # Threads 發布（每日）
```

---

## 🚀 部署指南

### 快速開始

1. **Fork** 此專案
2. 設定 **Secrets**（Settings → Secrets and variables → Actions）：

   | Secret | 用途 | 必要 |
   |--------|------|------|
   | `GFW_API_TOKEN` | Global Fishing Watch API | ✅ |
   | `THREADS_USER_ID` | Threads 發布 | 選配 |
   | `THREADS_ACCESS_TOKEN` | Threads 發布 | 選配 |
   | `THREADS_APP_SECRET` | Threads 發布 | 選配 |

3. 啟用 **GitHub Pages**（Settings → Pages → Source: GitHub Actions）
4. 手動觸發 **Actions → Update Gray Zone Vessel Data → Run workflow**
5. 完成！Dashboard：`https://<username>.github.io/taiwan-grayzone-monitor`

### GFW API Token 申請

前往 [Global Fishing Watch API](https://globalfishingwatch.org/our-apis/documentation) 註冊並取得 Token。

### Threads 發布設定（選配）

1. 建立 [Meta Developer App](https://developers.facebook.com/)
2. 啟用 Threads API 並取得 User ID、Access Token、App Secret
3. 填入 GitHub Secrets 即可自動發布

---

## 📊 監測範圍

```
        117°E          122°E          127°E
          │              │              │
  27°N ───┼──────────────┼──────────────┤
          │              │              │
          │     北區     │              │
          │   (台灣海峽  │              │
          │    北端)     │              │
  25°N ───┼──────────────┤              │
          │              │     東區     │
          │     西區     │   (太平洋    │
          │   (台灣海峽) │    側)       │
  23°N ───┼──────────────┤              │
          │              │              │
          │     南區     │              │
          │   (巴士海峽) │              │
  21°N ───┼──────────────┼──────────────┘
```

重點監測：台灣海峽、巴士海峽、蘭嶼東方海域、宮古海峽方向

---

## ⚠️ 免責聲明

- 本系統僅供**學術研究與公開情報分析**參考
- 不構成任何軍事、安全或投資判斷依據
- 資料可能因 API 限制、衛星覆蓋範圍、AIS 訊號品質而有延遲或不完整
- 「可疑」標記基於統計模型，非確認性指控

---

## 📚 參考資料

- [CSIS: Signals in the Swarm — Data Behind China's Maritime Gray Zone Campaign](https://www.csis.org/analysis/signals-swarm-data-behind-chinas-maritime-gray-zone-campaign-near-taiwan)
- [Global Fishing Watch API Documentation](https://globalfishingwatch.org/our-apis/)
- [台灣交通部航港局 Open Data](https://data.gov.tw/)
- [台灣海纜動態地圖](https://smc.peering.tw/)

---

## 🤝 Contributing

歡迎貢獻！你可以：

- 🐛 [回報問題](https://github.com/s0914712/taiwan-grayzone-monitor/issues)
- 💡 建議新功能或新的資料源整合
- 🔧 提交 Pull Request

---

## 📄 License

MIT License

---

<div align="center">

**Built with 🛰️ for open-source intelligence**

*Taiwan's maritime security deserves transparency.*

</div>
