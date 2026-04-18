# 🛰️ Taiwan Gray Zone Monitor | 台灣灰色地帶監測系統

<div align="center">

![Status](https://img.shields.io/badge/status-active-00f5ff?style=for-the-badge)
![License](https://img.shields.io/badge/license-MIT-blue?style=for-the-badge)
![Data](https://img.shields.io/badge/data-AIS%20%2B%20SAR%20%2B%20GFW-orange?style=for-the-badge)
![Auto Update](https://img.shields.io/badge/update-every%202hr-brightgreen?style=for-the-badge)

**即時整合 AIS、SAR 衛星與 GFW 資料的台灣周邊海域開源情報（OSINT）監測平台**
**Real-time OSINT monitoring platform integrating AIS, SAR satellite, and GFW data for Taiwan's surrounding waters**

[🌐 Live Demo](https://s0914712.github.io/taiwan-grayzone-monitor/) | [📊 Statistics 統計](https://s0914712.github.io/taiwan-grayzone-monitor/statistics.html) | [🔦 Dark Vessels 暗船](https://s0914712.github.io/taiwan-grayzone-monitor/dark-vessels.html) | [🚢 STS Detection 旁靠](https://s0914712.github.io/taiwan-grayzone-monitor/ship-transfers.html)

</div>

---

## 🔍 What is this? | 這個專案在做什麼？

China's gray zone operations against Taiwan extend far beyond military aircraft incursions — **maritime activity** is an equally critical front. This system automatically monitors anomalous vessel behavior in Taiwan's surrounding waters through multiple open data sources.

中國對台灣的灰色地帶行動不僅限於軍機擾台，**海上活動**同樣是關鍵戰場。本系統透過多重公開資料源，自動監測台灣周邊海域的異常船隻行為。

| Threat Type 威脅類型 | Description 說明 | Detection Method 偵測方式 |
|---|---|---|
| 🔦 **Dark Vessels 暗船** | Vessels with AIS transponders turned off, still detectable by satellite 關閉 AIS 的船隻，衛星仍可偵測 | GFW SAR satellite imagery analysis GFW SAR 衛星影像分析 |
| 🚨 **Suspicious Vessels 可疑船隻** | Behavior matching gray zone operation characteristics 行為符合灰色地帶行動特徵 | Automated CSIS methodology scoring CSIS 方法論自動判定 |
| 🚢 **Ship-to-Ship Transfer 旁靠偵測** | Vessels alongside each other (<10m) at sea for extended periods 船舶在海上近距離旁靠（<10m）持續一段時間 | Spatiotemporal pair analysis 時空配對分析 |
| 🔄 **AIS Identity Change 身分變更** | Vessels changing name, MMSI, or flag state 船隻更改名稱/MMSI/旗幟 | Historical snapshot comparison 歷史快照比對 |
| 🎣 **Fishing Fleet Aggregation 漁船聚集** | Chinese fishing vessels clustering in sensitive waters 中國漁船在敏感海域群聚 | Fishing hotspot analysis 漁撈熱點分析 |
| 🔗 **Submarine Cable Threats 海底電纜威脅** | Cable fault and disruption monitoring 電纜斷裂/故障監測 | Real-time cable status tracking 電纜狀態追蹤 |
| 📐 **Territorial Baseline 領海基線** | Official basepoints defining Taiwan's territorial sea 內政部公告領海基點圖層 | MOI geodetic data overlay 內政部國土測繪資料疊加 |

### Methodology 方法論

Based on the CSIS (Center for Strategic and International Studies) report **["Signals in the Swarm"](https://www.csis.org/analysis/signals-swarm-data-behind-chinas-maritime-gray-zone-campaign-near-taiwan)**:

基於 CSIS（戰略與國際研究中心）報告 **[「Signals in the Swarm」](https://www.csis.org/analysis/signals-swarm-data-behind-chinas-maritime-gray-zone-campaign-near-taiwan)**：

> **Criteria 判定標準:** Time in exercise zones > 30%, time in fishing hotspots < 10%, AIS anomalies
> 軍演區停留 > 30%、漁撈熱點停留 < 10%、AIS 異常行為

---

## 📱 Features 功能特色

### 1. Real-time Monitoring Dashboard 即時監測儀表板

| Feature 功能 | Description 說明 |
|---|---|
| **Daily Overview 今日概況** | At-a-glance stats: AIS vessel count, dark vessels, suspicious vessels, identity changes 一眼掌握 AIS 船隻數、暗船、可疑船隻、身分變更 |
| **Smart Zoom 智慧縮放** | Region-level aggregate stats at full view; individual vessel markers when zoomed in 全區域顯示分區聚合統計，拉近後顯示個別船隻 |
| **Multi-layer Overlay 多圖層疊加** | Toggle AIS vessels, dark vessels, fishing hotspots, submarine cables, territorial baseline, vessel routes 自由切換 AIS 船隻、暗船、漁撈熱點、海底電纜、領海基線、船隻航跡 |
| **Vessel Track Lookup 船隻航跡追蹤** | Search any MMSI to display historical route on map 輸入 MMSI 即可在地圖上顯示歷史航跡 |
| **FOC Vessel Filter 權宜船過濾** | Highlight or filter Flag of Convenience commercial vessels 標記或過濾權宜旗商船 |
| **UN Sanctions Alert 聯合國制裁警示** | Automatic cross-reference with UN sanctions list 自動比對聯合國制裁名單 |
| **Bilingual UI 中英雙語** | Full Chinese/English interface with one-click toggle 完整中英文介面，一鍵切換 |

### 2. Dark Vessel Analysis 暗船深度分析

| Feature 功能 | Description 說明 |
|---|---|
| **Regional Density Map 區域密度圖** | Heatmap of dark vessel concentration by maritime zone 各海域暗船密度熱力圖 |
| **Trend Charts 趨勢圖表** | 90-day historical dark vessel detection trends 90 天暗船偵測歷史趨勢 |
| **SAR Satellite Data SAR 衛星資料** | Synthetic Aperture Radar detections from Global Fishing Watch 來自 GFW 的合成孔徑雷達偵測資料 |

### 3. Ship-to-Ship Transfer Detection 旁靠偵測

| Feature 功能 | Description 說明 |
|---|---|
| **Proximity Detection 近距離偵測** | Detect vessel pairs within 10 meters at sea 偵測海上距離 < 10 公尺的船對 |
| **Classification 分類判定** | Automatically classify as suspicious transfer vs. pair trawling 自動區分可疑物資傳遞與雙拖作業 |
| **Risk Scoring 風險評分** | Multi-factor risk assessment (vessel type, location, duration, behavior) 多因子風險評估（船型、位置、時長、行為） |
| **Historical Track Overlay 歷史航跡疊加** | Click any vessel in a transfer event to view its full route on the map 點擊旁靠事件中的船舶即可在地圖上查看完整航跡 |
| **Port Exclusion 港口排除** | Automatically exclude events within 2km of known ports 自動排除已知港口 2 公里內的事件 |

### 4. AIS Identity Tracking 身分追蹤

| Feature 功能 | Description 說明 |
|---|---|
| **Identity Change Timeline 身分變更時間線** | Chronological display of vessel name, MMSI, and flag changes 按時間順序顯示船名、MMSI、旗幟變更事件 |
| **Change Type Filtering 變更類型篩選** | Filter by name change, MMSI change, flag change, or type change 依名稱、MMSI、旗幟、船型變更分類篩選 |
| **Up to 5,000 Events 最多 5,000 筆事件** | Maintains a rolling history of identity manipulation events 維護身分操縱事件的滾動歷史紀錄 |

### 5. Statistics Dashboard 統計儀表板

| Feature 功能 | Description 說明 |
|---|---|
| **Vessel Type Distribution 船型分布** | Breakdown by fishing, cargo, tanker, LNG, and other vessel types 依漁船、貨船、油輪、LNG 等船型分類 |
| **Flag State Analysis 旗幟國分析** | Country-of-registration statistics and Flag of Convenience identification 船籍國統計與權宜旗識別 |
| **Historical Trends 歷史趨勢** | Long-term vessel activity and anomaly trend charts 長期船隻活動與異常趨勢圖表 |
| **Regional Breakdown 分區統計** | Activity statistics by maritime zone (Strait, Pacific, Bashi Channel) 依海域分區統計（海峽、太平洋側、巴士海峽） |

### 6. Animation Playback 動畫回放

| Page 頁面 | Description 說明 |
|---|---|
| **Dark Vessel Animation 暗船動畫** | 90-day dark vessel activity hotspot evolution with playback controls 90 天暗船活動熱點變化，含播放控制 |
| **AIS Position Animation AIS 船位動畫** | Real-time vessel position replay across Taiwan's waters AIS 即時船位動態回放 |
| **Chinese Fishing Fleet Animation 中國漁船動畫** | Track Chinese fishing vessel activity range changes over time 追蹤大陸漁船活動範圍隨時間變化 |

### 7. Automated Reports & Social Publishing 自動報告 & 社群發布

| Feature 功能 | Description 說明 |
|---|---|
| **Daily/Weekly Summary 每日/每週摘要** | Automated structured report generation 自動產生結構化報告 |
| **Threads Auto-publishing Threads 自動發布** | Daily posts at 08:30 with AI-generated captions and charts 每日 08:30 自動發文，搭配 AI 生成文案與圖表 |
| **Social Sharing 社群分享** | One-click share to Twitter/X and LINE 一鍵分享到 Twitter/X、LINE |

---

## 🖥️ Architecture 系統架構

```
                    ┌─────────────────────────────────┐
                    │         GitHub Actions           │
                    │   (every 2hr / every 12hr)       │
                    │   (每 2 小時 / 每 12 小時)         │
                    └────────┬────────────────────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
     ┌────────────┐  ┌────────────┐  ┌────────────┐
     │  AIS Live  │  │  GFW SAR   │  │ Cable      │
     │  Positions │  │  Dark      │  │ Status     │
     │  AIS 即時  │  │  Vessels   │  │ 海纜狀態   │
     │  船位資料  │  │  暗船資料  │  │            │
     └─────┬──────┘  └─────┬──────┘  └─────┬──────┘
           │               │               │
           ▼               ▼               ▼
     ┌─────────────────────────────────────────────┐
     │  Python Data Pipeline 資料管線               │
     │  • analyze_suspicious.py (CSIS scoring)     │
     │  • detect_ship_transfers.py (STS detection) │
     │  • exercise_prediction.py (PLA correlation) │
     │  • generate_dashboard.py (consolidation)    │
     └─────────────┬───────────────────────────────┘
                   │
          ┌────────┴────────┐
          ▼                 ▼
   ┌─────────────┐  ┌──────────────┐
   │ GitHub Pages │  │   Threads    │
   │  Dashboard   │  │  Publishing  │
   │  前端儀表板  │  │  社群發布    │
   └─────────────┘  └──────────────┘
```

### Tech Stack 技術棧

| Component 組件 | Technology 技術 |
|---|---|
| Frontend 前端 | Vanilla HTML/CSS/JS, Leaflet.js 1.9.4, Chart.js 4.4.0 |
| Map Tiles 地圖底圖 | CartoDB Dark Matter |
| Data Pipeline 資料管線 | Python 3.11 (pandas, requests, scipy, matplotlib) |
| Automation 自動化 | GitHub Actions (3 workflows) |
| Hosting 部署 | GitHub Pages (zero-cost, zero-server 零成本、零伺服器) |
| Social 社群 | Threads Graph API + Google Gemini (AI captions) |

### Data Update Frequency 資料更新頻率

| Data Source 資料源 | Frequency 頻率 | Description 說明 |
|---|---|---|
| AIS Live Positions AIS 即時船位 | Every 2 hours 每 2 小時 | Taiwan Port Bureau Open Data 台灣港務局開放資料 |
| GFW SAR Dark Vessels SAR 暗船 | Every 12 hours 每 12 小時 | Satellite radar imagery analysis 衛星雷達影像分析 |
| CSIS Suspicious Analysis 可疑分析 | Every 12 hours 每 12 小時 | Automated methodology scoring 自動方法論判定 |
| STS Transfer Detection 旁靠偵測 | Every 2 hours 每 2 小時 | Vessel pair proximity analysis 船對近距離分析 |
| AIS Identity Changes 身分變更 | Every 2 hours 每 2 小時 | Historical snapshot diff 歷史快照差異比對 |
| Threads Report 社群報告 | Daily 08:30 每日 08:30 | Auto-summary + charts 自動摘要 + 圖表 |

---

## 🗂️ Project Structure 專案結構

```
taiwan-grayzone-monitor/
├── docs/                              # Frontend (GitHub Pages root) 前端
│   ├── index.html                     # Main dashboard 主監測儀表板
│   ├── dark-vessels.html              # Dark vessel analysis 暗船深度分析
│   ├── statistics.html                # Statistics dashboard 統計儀表板
│   ├── ship-transfers.html            # STS transfer detection 旁靠偵測
│   ├── identity-history.html          # AIS identity tracking 身分追蹤
│   ├── weekly-animation.html          # Dark vessel 90-day animation 暗船動畫
│   ├── ais-animation.html             # AIS position animation 船位動畫
│   ├── cn-fishing-animation.html      # Chinese fishing fleet animation 大陸漁船動畫
│   ├── intro.html                     # Project introduction 專案介紹
│   ├── js/
│   │   ├── app.js                     # Main controller 應用控制器
│   │   ├── map.js                     # Leaflet map module 地圖模組
│   │   ├── charts.js                  # Chart.js integration 圖表模組
│   │   ├── i18n.js                    # Internationalization ZH/EN 國際化
│   │   └── mobile-nav.js             # Shared mobile navigation 行動版導航
│   ├── css/main.css                   # Site-wide stylesheet 全站樣式
│   ├── data.json                      # Consolidated monitoring data 彙整監測資料
│   ├── ship_transfers.json            # STS detection results 旁靠偵測結果
│   ├── ais_history.json               # 90-day AIS snapshots AIS 歷史快照
│   ├── ais_track_history.json         # 14-day full track history 完整航跡歷史
│   ├── identity_events.json           # AIS identity change events 身分變更事件
│   ├── weekly_dark_vessels.json       # 90-day SAR detections 暗船偵測紀錄
│   ├── cable_status.json              # Submarine cable status 海纜狀態
│   ├── taiwan_cables.json             # Cable route GeoJSON 海纜路線
│   └── vessel_routes/                 # 1,000+ per-vessel route files 個別船隻航跡
├── src/                               # Python data pipeline 資料管線
│   ├── fetch_ais_data.py              # Fetch AIS positions 擷取 AIS 船位
│   ├── fetch_gfw_data.py              # Fetch GFW SAR data 擷取 GFW SAR 資料
│   ├── analyze_suspicious.py          # CSIS suspicious scoring 可疑船隻分析
│   ├── detect_ship_transfers.py       # STS transfer detection 旁靠偵測
│   ├── generate_dashboard.py          # Consolidate to data.json 產生前端資料
│   ├── extract_all_routes.py          # Batch extract vessel routes 批次擷取航跡
│   ├── extract_vessel_route.py        # Single vessel route 單船航跡擷取
│   ├── exercise_prediction.py         # PLA exercise prediction 軍演預測分析
│   ├── fetch_weekly_dark_vessels.py   # Weekly dark vessel animation data 週暗船動畫資料
│   ├── generate_summary.py            # Daily/weekly report 每日/週摘要報告
│   └── publish_threads.py            # Threads auto-publishing 自動發布
└── .github/workflows/
    ├── update-ais.yml                 # AIS update (every 2hr) AIS 更新
    ├── update-data.yml                # Full pipeline (every 12hr) 全量更新
    └── publish-threads.yml            # Threads publishing (daily) 社群發布
```

---

## 📊 Monitoring Area 監測範圍

```
        117°E          122°E          127°E
          │              │              │
  27°N ───┼──────────────┼──────────────┤
          │              │              │
          │    North 北區  │              │
          │  (N. Taiwan   │              │
          │   Strait)     │              │
  25°N ───┼──────────────┤              │
          │              │   East 東區   │
          │   West 西區   │  (Pacific    │
          │  (Taiwan     │   Side)      │
          │   Strait)    │              │
  23°N ───┼──────────────┤              │
          │              │              │
          │   South 南區  │              │
          │  (Bashi      │              │
          │   Channel)   │              │
  21°N ───┼──────────────┼──────────────┘
```

**Key areas 重點監測：**
Taiwan Strait 台灣海峽 · Bashi Channel 巴士海峽 · East of Lanyu 蘭嶼東方海域 · Miyako Strait direction 宮古海峽方向

---

## 🚀 Deployment Guide 部署指南

### Quick Start 快速開始

1. **Fork** this repository 複製此專案
2. Configure **Secrets** (Settings → Secrets and variables → Actions):
   設定 **Secrets**（Settings → Secrets and variables → Actions）：

   | Secret | Purpose 用途 | Required 必要 |
   |---|---|---|
   | `GFW_API_TOKEN` | Global Fishing Watch API | ✅ Yes 是 |
   | `THREADS_USER_ID` | Threads publishing 發布 | Optional 選配 |
   | `THREADS_ACCESS_TOKEN` | Threads publishing 發布 | Optional 選配 |
   | `THREADS_APP_SECRET` | Threads publishing 發布 | Optional 選配 |
   | `GEMINI_API_KEY` | AI-generated captions AI 文案 | Optional 選配 |

3. Enable **GitHub Pages** (Settings → Pages → Source: GitHub Actions)
   啟用 **GitHub Pages**（Settings → Pages → Source: GitHub Actions）
4. Manually trigger **Actions → Update Gray Zone Vessel Data → Run workflow**
   手動觸發 **Actions → Update Gray Zone Vessel Data → Run workflow**
5. Done! Your dashboard is live at `https://<username>.github.io/taiwan-grayzone-monitor`
   完成！儀表板：`https://<username>.github.io/taiwan-grayzone-monitor`

### GFW API Token

Register and obtain a token at [Global Fishing Watch API](https://globalfishingwatch.org/our-apis/documentation).
前往 [Global Fishing Watch API](https://globalfishingwatch.org/our-apis/documentation) 註冊並取得 Token。

### Threads Publishing Setup (Optional) Threads 發布設定（選配）

1. Create a [Meta Developer App](https://developers.facebook.com/)
   建立 [Meta Developer App](https://developers.facebook.com/)
2. Enable Threads API and obtain User ID, Access Token, and App Secret
   啟用 Threads API 並取得 User ID、Access Token、App Secret
3. Add them to GitHub Secrets — posts will publish automatically
   填入 GitHub Secrets 即可自動發布

### Local Development 本地開發

```bash
# Fetch AIS data 擷取 AIS 資料
python3 src/fetch_ais_data.py

# Fetch GFW SAR data 擷取 GFW SAR 資料
python3 src/fetch_gfw_data.py

# Run CSIS suspicious analysis 執行 CSIS 可疑分析
python3 src/analyze_suspicious.py

# Run STS transfer detection 執行旁靠偵測
python3 src/detect_ship_transfers.py

# Consolidate to frontend data 彙整前端資料
python3 src/generate_dashboard.py

# Batch extract vessel routes 批次擷取航跡
python3 src/extract_all_routes.py

# Generate daily report 產生每日報告
python3 src/generate_summary.py --mode daily

# Test Threads post (dry run) 測試 Threads 發文
python3 src/publish_threads.py --dry-run
```

---

## ⚠️ Disclaimer 免責聲明

- This system is intended for **academic research and open-source intelligence analysis** only.
  本系統僅供**學術研究與公開情報分析**參考。
- It does not constitute any basis for military, security, or investment decisions.
  不構成任何軍事、安全或投資判斷依據。
- Data may be delayed or incomplete due to API limitations, satellite coverage, or AIS signal quality.
  資料可能因 API 限制、衛星覆蓋範圍、AIS 訊號品質而有延遲或不完整。
- "Suspicious" labels are based on statistical models, not definitive accusations.
  「可疑」標記基於統計模型，非確認性指控。

---

## 📚 References 參考資料

- [CSIS: Signals in the Swarm — Data Behind China's Maritime Gray Zone Campaign](https://www.csis.org/analysis/signals-swarm-data-behind-chinas-maritime-gray-zone-campaign-near-taiwan)
- [Global Fishing Watch API Documentation](https://globalfishingwatch.org/our-apis/)
- [Taiwan Ministry of Transportation Port Bureau Open Data 交通部航港局開放資料](https://data.gov.tw/)
- [Taiwan Submarine Cable Map 台灣海纜動態地圖](https://smc.peering.tw/)
- [Taiwan MOI Territorial Basepoints 內政部領海基點](https://opdadm.moi.gov.tw/)

---

## 🤝 Contributing 參與貢獻

Contributions are welcome! 歡迎貢獻！

- 🐛 [Report issues 回報問題](https://github.com/s0914712/taiwan-grayzone-monitor/issues)
- 💡 Suggest new features or data source integrations 建議新功能或新資料源整合
- 🔧 Submit a Pull Request 提交 PR

---

## 📄 License 授權

MIT License

---

<div align="center">

**Built with 🛰️ for open-source intelligence**

*Taiwan's maritime security deserves transparency.*
*台灣的海洋安全值得被看見。*

</div>
