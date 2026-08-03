# CLAUDE.md — Taiwan Gray Zone Monitor

## Overview
Real-time OSINT monitoring of Taiwan's gray zone maritime activity. Integrates AIS vessel data, GFW SAR satellite imagery, and CSIS threat methodology. Static site on GitHub Pages with Python data pipelines automated via GitHub Actions.

**Sub-directory docs** (auto-loaded when working in those dirs):
- `src/CLAUDE.md` — Python pipeline details, data structures, file-by-file reference
- `docs/CLAUDE.md` — Frontend architecture, JS modules, CSS design system, z-index, i18n

## Directory Structure
- `docs/` — Frontend (GitHub Pages root). HTML, CSS, JS, and JSON data files
- `src/` — Python data pipeline scripts (fetch, analyze, generate)
- `data/` — Working/intermediate data (not in the Pages artifact). `data/vessel_routes/{mmsi}.json` is gitignored on main and lives on the single-commit **`vessel-data` branch** (CI regenerates + force-pushes it each run); the frontend fetches routes via raw.githubusercontent.com/.../vessel-data/ — 27k route files in main history bloated the repo to 200MB+ and made Pages deployments time out
- `.github/workflows/` — 6 CI workflows (AIS hourly daytime / 2h overnight, full pipeline every 12h incl. once-daily 00:00 UTC gov-vessel track map, **網路異常掃描 every 2h（Cloudflare Radar 國家級 + IODA 離島縣市級）**, darkship SAR forensics daily 22:00 UTC, Threads weekly, LINE daily push 00:00 UTC = 08:00 TW). All data workflows share the `data-pipeline` concurrency group — they commit to main and would otherwise race on rebase
- `worker/` — **LINE 到工統計助手**（Cloudflare Worker，與資料管線無關）。唯一需要 24h 在線的元件：GitHub Actions 是 cron，收不到 LINE webhook，而讀取群組回覆非要 webhook 不可。詳見 `worker/README.md` 與下方章節
- `chips/` + `reports/` — darkship SAR forensics outputs (chip PNGs, `chips/results.json` cumulative log, daily Markdown reports), committed by `darkship-cron.yml`; **not** in the Pages artifact but public in the repo — a deliberate trade-off chosen when the cron was set up. The public daily report page (`docs/reports/<date>.html`, `generate_report.py`) surfaces this work: SAR×AIS 比對成效 funnel + the latest run's chip images (520px thumbnails in `docs/reports/chips/`, 14-day mtime rotation, `<img onerror>` falls back to the raw.githubusercontent original) with verdict badges

## Tech Stack
- **Frontend:** Vanilla HTML/CSS/JS, Leaflet 1.9.4 (maps), Chart.js 4.4.0 (charts)
- **Backend:** Python 3.11 (pandas, requests, scipy, matplotlib)
- **Hosting:** GitHub Pages (zero-build, static)
- **APIs:** Taiwan Port Bureau (AIS), Global Fishing Watch (SAR), Threads Graph API

## Data Flow
```
GitHub Actions → src/fetch_ais_data.py (AIS via SOCKS5 proxy)
              → src/fetch_gfw_data.py (SAR dark vessels)
              → src/match_sar_ais.py (re-match GFW dark detections vs local AIS)
              → src/detect_ship_transfers.py (STS rendezvous detection)
              → src/analyze_suspicious.py (threat scoring)
              → src/exercise_prediction.py (PLA sortie correlation)
              → src/extract_all_routes.py (per-vessel route JSONs)
              → src/fetch_cloudflare_radar.py (網路流量異常 × 海纜旁滯留船隻)
              → src/fetch_ioda.py (金門/馬祖/澎湖離島可達性，三來源互相印證)
              → src/generate_dashboard.py (consolidate → docs/data.json)
              → GitHub Pages deploy
```

---

## analyze_suspicious.py — Threat Scoring Engine

### Architecture
The engine loads multiple data sources, iterates all known vessels (profile ∪ track), and produces a per-vessel risk classification.

**Active-vessel window**: only vessels seen within `ANALYSIS_ACTIVE_DAYS` (14) — has track
points or profile `last_seen_timestamps[-1]` within the window — are analyzed
(`is_recently_active()`). Profiles are retained 90 days, but scoring long-gone vessels on
stale data inflated the stats (55k analyzed → dashboard "Top 10%" showed fleet÷10 ≈ 2.5-3.4k).
Summary carries `stale_skipped` + `active_window_days`; the dashboard stat tile now shows
`suspicious_count` (score ≥8) instead of the quota-based `top_10pct_count`.

**Data sources loaded:**
1. `vessel_profiles.json` — AIS-observed vessel metadata (names, types, timestamps)
2. `ais_track_history.json` (tier-1) — CN fishing + suspicious vessel tracks (14 days)
3. `ais_track_commercial.json` (tier-2) — cargo/tanker/LNG + identity-changed vessel tracks
4. `cable-geo.json` — Submarine cable route GeoJSON
5. `identity_events.json` — AIS identity change events (7-day window)
6. `un_sanctions_vessels.json` — UN 1718 sanctions vessel list (IMO **and** name match)
6b. `sanctions_blacklist.json` — multi-agency shadow-fleet tanker blacklist (1400+ vessels: OFAC/UK-FCDO/UANI/EU/SECO/MFAT…), built from `sanctions_blacklist.csv` by `build_sanctions_blacklist.py`. **IMO-match only** (name matching disabled — 1400+ common names collide); merged into the sanction IMO set by `load_sanctions_list()`. On an IMO hit whose AIS-broadcast name ≠ the sanctions-registered name, an identity-concealment flag is raised (`sanction_identity_concealment`)
7. `itu_mars_cache.json` — ITU MARS ship station registry cache (30-day expiry)
8. `ship_transfers.json` — STS rendezvous detection results

### Two-Tier Track Storage
- **Tier-1** (`ais_track_history.json`): CN fishing vessels + suspicious → used for animation + analysis
- **Tier-2** (`ais_track_commercial.json`): cargo, tanker, LNG, identity-changed vessels → analysis + route extraction only
- Both files: append-and-trim, 14-day / 168-entry max retention per tier

### Exclusion Rules (early return, skips expensive analysis)
Defined in `EXCLUSION_RULES` list. Each rule is a dict with `id`, `label`, `check(mmsi, names) -> bool`.
Current rules:
- MMSI starts with `9` (AtoN/diving buoys)
- MMSI starts with `898` (fishing net markers)
- Name contains `%` (fishing net beacons)
- Name contains `BUOY`
- Name ends with voltage pattern like `12.5V` (fishing net beacons)
- Name ends with `digits%` pattern

**To add a new exclusion rule:** Append a dict to `EXCLUSION_RULES` list. No other code changes needed.

**Taiwan-vessel exclusion** (in `classify_vessel`, not `EXCLUSION_RULES` — needs track/event
data): vessels are also excluded when (a) the MMSI MID is `416` (Taiwan flag,
`flag_taiwan`), or (b) the **last position** is inside a Taiwan port from `geofence.PORTS`
(`moored_taiwan_port`; CN ports do NOT trigger this). **Anti-spoofing safety valve**: a
vessel matching either rule is still fully analyzed if it has identity-change events
(7-day window) or a UN sanctions hit — a CN vessel broadcasting a fake `416` MMSI must
not escape detection. Both ids appear in the output's `exclusion_rules` list and
`summary.exclusion_breakdown`.

### Scoring Criteria (8 criteria)

| # | Criterion | Detection Method | Raw Score |
|---|-----------|-----------------|-----------|
| 1a | Cable Proximity | Track points within 5km of submarine cable (bbox pre-filtered, **in-port points excluded**) | +2 |
| 1b | Cable Loitering | Low speed (<5kn) near cable for >3hr **continuous** (slow-timestamp runs split when gap >4h; in-port excluded) | +3 |
| 2 | Zigzag Pattern | ≥3 turns of ≥45° heading change (calc_bearing from positions; anchored `anc` / <1kn points filtered — anchor swing is not zigzag) | +1 |
| 3 | 200m Depth Contour | ≥30% of track time near continental shelf edge | +1 |
| 4 | AIS Anomalies | Name changes ≥2, going dark >18hr gaps, type changes, identity events | +1 (medium) / +3 (high) |
| 5 | Non-Top-10 Flag | MMSI MID not in top-10 flag state set | +1 |
| 6 | Sanctions (UN 1718 + multi-agency shadow-fleet blacklist) | IMO match +8 (high confidence); UN name-only match +4 (Chinese ship names collide often). Blacklist is **IMO-only**; on IMO hit with AIS name ≠ registered name → extra identity-concealment flag | +4/+8 |
| 7 | AIS Spoofing | Impossible physics / box pattern / circle pattern (see false-positive suppression below) | +4 each |
| 8 | ITU MARS Mismatch | Ship name, IMO, or call sign differs from ITU registry | +3 |
| 9 | STS Transfer | Involved in ship-to-ship rendezvous (suspicious: +5, any: +2) | +2/+5 |
| 10 | Offshore Loitering | Commercial vessel (tanker/cargo/lng) milling offshore ≥5 days (`check_offshore_loitering`: >50% points <3kn, median radius from centre ≤20km, low-speed run spans ≥5 days, in-port excluded) — **cable-independent** shadow-fleet standby pattern. Scored **only when also non-top-10 flag** (FoC); plain loitering may be legitimate berth-waiting. | +4 |

**In-port suppression:** cable landings sit next to ports, so a ship legally moored in
Kaohsiung would otherwise score ~9 (proximity + loiter + combos + buffers) and cross the
suspicious threshold. `annotate_port_points()` marks every track point via
`geofence.is_in_port_cached()` (Taiwan ports 2km / CN coastal ports 8km+, shared with STS
detection — the port list lives in `geofence.py`); those points are skipped for criteria
1a/1b and the geofence buffer bonuses. Track points also carry the `anc` flag
(nav_status 1=at anchor / 5=moored, written by `fetch_ais_data.py`).

### Vessel Type Multiplier
Applied to **behavioral scores only** (criteria 1-3, 5). High-threat indicators (4, 6-9) are NOT multiplied.

| Type | Multiplier | Rationale |
|------|-----------|-----------|
| cargo, tanker, lng | ×1.0 | Long anchor chains, high tonnage → real cable damage risk |
| fishing | ×0.2 | Small, routine operations, low cable threat |
| coastguard, msa, rescue, research | ×0.5 | China public-service / special-interest state vessels |
| other, unknown | ×0.5 | Uncertain |

**China gov / special-interest vessel detection:** `classify_gov_vessel(name)` in `fetch_ais_data.py` flags Chinese state vessels by name keyword and returns a sub-category:
| Category | Keywords | Frontend color / badge |
|----------|----------|------------------------|
| `coastguard` 海警 | `COAST GUARD`, `CCG\d*`, `HAIJING`, `海警`, `CHINA COAST` | white / 🛡️ |
| `msa` 海巡 (海事局) | `HAIXUN`, `海巡` | blue `#4d9fff` / ⚓ |
| `rescue` 海救 (救助局) | `(DONG\|NAN\|BEI)HAIJIU`, `海救` | orange `#ff9500` / 🛟 |
| `research` 科研/情報 | `XIANGYANGHONG`, `DONGFANGHONG`, `\bTONGJI\b`, `\bKEXUE\b`, `SHIYAN`, `TANSUO`, 向陽紅/東方紅/同濟/實驗/探索… | purple `#c77dff` / 🔬 |

On match `type_name` is overridden to the category and a `gov_type` field (+ `is_coast_guard` for the coastguard sub-type) is set; tier-1 tracks gain a `gov:<category>` flag and these vessels are always retained in tier-1 (so routes accumulate). MMSI-prefix matching is deliberately **not** used (block `413875xxx` is shared with civilian vessels). Two supplementary paths exist beyond name keywords: (1) `KNOWN_GOV_MMSI` — an **exact-MMSI** (not prefix) table of individually-evidenced gov vessels (e.g. `413875010` = 海警1401, observed broadcasting CHINACOASTGUARD1401 on 2026-07-11), consulted when the name doesn't match, so a known cutter that switches to a numeric/meaningless name is still classified; (2) `is_gov_candidate()` — a **pure-digit ship name** (3–5 digits, e.g. "1401") + China MID (412/413/414) sets a `gov_candidate` flag and forces tier-1 retention (compact `cand:1` track flag) for manual review, without auto-classifying. "Name contains 4 digits" is NOT usable as a signal — CN fishing fleets use digit suffixes (MINXIAYU01401 is a fishing boat). Research keywords use word boundaries (`\b`) to avoid false hits (e.g. `AN TONG JING TANG` must not match `TONG JI`). Taiwan CGA (海巡署) is intentionally excluded. `plot_gov_vessel_tracks.py` renders combined historical tracks (colored by category) to `docs/cn_gov_vessel_tracks.png`.

### Combo Bonuses (also multiplied by vessel type)
- Cable proximity + zigzag: +3 (possible anchor dragging) — **only if ≥3 turns happened
  at ≤7kn** (`ANCHOR_DRAG_MAX_KNOTS`; a ship doing >7kn almost certainly has no anchor
  down — high-speed zigzag is fishing/maneuvering, not anchor dragging)
- Cable proximity + loitering: +2

### Geofence Buffer Bonuses (behavioral, multiplied by vessel type)
Refine the coarse 5km cable net using the per-vessel `geofence` annotation
(`src/geofence.py`, from the **last** position). Bounded, additive:
- Last position ≤1km from a cable (`cable_band == within_1km`): **+1**
- …and inside Taiwan's jurisdiction (zone ∈ internal_waters / territorial_sea /
  contiguous_zone): **+1** more — the precise gray-zone cable-threat scenario.
Constants: `CABLE_BUFFER_1KM_SCORE`, `CABLE_BUFFER_JURISDICTION_SCORE`,
`JURISDICTION_ZONES`. Each scored vessel carries `cable_buffer_1km` /
`cable_buffer_jurisdiction` booleans; summary adds the two trigger counts.
**Not awarded when the last position is in port** — a berthed ship is always in
internal waters near a cable landing; that is routine, not the gray-zone scenario.

### Final Score & Risk Levels
```
final_score = round(raw_behavioral_score × type_multiplier) + high_threat_indicators
```

| Level | Score | Meaning |
|-------|-------|---------|
| critical | ≥12 | Multiple strong indicators |
| high | ≥8 | Suspicious — flagged in output |
| medium | ≥5 | Elevated but not suspicious |
| normal | <5 | No action |

**Suspicious threshold: score ≥ 8**

### Spoofing Detection Details

**Impossible Physics** (`check_impossible_physics`):
- Teleportation: calculated speed > 100 km/h between consecutive points. If the two
  points carry **different vessel names**, it's counted as `mmsi_collision` (shared MMSI,
  common among CN fishing fleets) instead of a teleport — not spoofing.
- Speed mismatch (calc_speed / reported_SOG ratio > 3× or < 0.33×) and bearing mismatch
  (calc_bearing vs reported COG > 60°) are **only evaluated when dt ≤ 1h**
  (`PHYSICS_MISMATCH_MAX_DT_HOURS`) — at the normal 2h snapshot cadence, comparing an
  instantaneous SOG against a 2h average speed only produces false positives.
- Skips going-dark gaps (>18h) to avoid false positives

**Box Pattern** (`check_box_pattern`):
- ≥3 near-90° turns (65°-115° tolerance)
- Path closed (start-end < 5km) or bounding box < 5km
- Filters stationary points (speed < 0.5kn)
- Suppressed when the pattern centroid is **in port** (berthing/anchorage maneuvering
  is right-angle turns in a small closed area by nature)

**Circle Pattern** (`check_circle_pattern`):
- Centroid-based radius CV < 0.25 (low variation = symmetric)
- Arc coverage > 270° (near-complete circle)
- Radius range: 0.1-5.0 km (excludes GPS drift and normal sailing)
- **Anchor-swing suppression**: radius ≤0.6km and (≥half points flagged `anc` or median
  speed <2kn) → a ship swinging on its anchor, not spoofing
- Suppressed when the centroid is **in port** (port-area GPS interference "crop circles"
  are environmental, not vessel-intent spoofing)

### Output
- `data/suspicious_vessels.json` — Top 50 suspicious vessels + top 200 classifications + full summary stats
- Summary includes per-criterion trigger counts, risk distribution, exclusion breakdown

### Performance Optimization
- **Exclusion early return**: Buoys/beacons skip all expensive analysis
- **Cable bbox pre-filter**: Only cables whose bounding box overlaps vessel track are checked
- Cable proximity: O(track_points × nearby_cable_segments) instead of all cables

---

## match_sar_ais.py — SAR × Local-AIS Re-matching (dark-vessel de-noising)

GFW's matched/unmatched flag uses GFW's own AIS feed; the Port Bureau AIS feed is
denser near Taiwan. This module re-matches every GFW-**unmatched** SAR detection
against local AIS to remove false dark ships. Runs in `update-data.yml` right after
`fetch_gfw_data.py` (which now also writes `data/sar_detections.json` — the full,
full-precision unmatched detection list; `dark_vessels.json`'s 400 rounded samples
are only a fallback).

Pipeline (per detection):
1. **Fixed-infrastructure filter** — wind turbines/aquaculture platforms detected as
   ships. Two mechanisms:
   - **Cross-run recurrence** (`data/sar_detection_history.json`): within a single
     30-day GFW window fixed infrastructure does **not** recur — the aggregated
     detections are full-precision and every one lands in a unique 0.01° cell (a wind
     farm shows up instead as many *same-date* regularly-spaced array detections,
     imaged once per window). The recurrence signal only emerges across cron runs, so
     detections are accumulated per **0.03° cell** (`INFRA_CELL_DEG`; S1 geolocation
     ~0.5km ≪ 3km, so a fixed target's centroid falls back in the same cell across
     passes) over `HISTORY_RETENTION_DAYS` (90); a cell seen on ≥ `INFRA_MIN_DATES`
     (3) distinct overpass dates → infrastructure (`update_detection_history` +
     `recurring_cells_from_history`, both pure). History is committed via
     `update-data.yml`'s `git add data/`, mirroring `dark_vessel_history.json`.
     `detect_infrastructure_cells` (single-window) is kept only as the fallback when no
     history is passed (synthetic tests).
   - **Static mask** `data/fixed_infrastructure.json` (`{"points":[{lat, lon,
     radius_km}]}`, `load_fixed_infra_mask()`) — committed hand-authored seed of known
     offshore wind farm zones (Taiwan Changhua/Miaoli, Fujian, Jiangsu Rudong; grounded
     in the observed same-date array signature), the immediate deterministic backstop
     before history accumulates. Extend with a GFW fixed-infrastructure export when
     available.
   Summary carries `infrastructure_filtered` (recs removed), `infrastructure_cells_detected`
   (recurring cells), and `infra_source` (`history`/`window`).
2. **Time interpolation / dead reckoning** — SAR imaging is instantaneous; AIS is a 2h
   snapshot cadence. AIS tracks (tier-1 + tier-2 + snapshot; AtoN/buoy/net-beacon
   transmitters excluded) are interpolated to the SAR overpass instant. Overpass time
   priority: per-detection timestamp > **real Sentinel-1 pass times from NASA CMR**
   (`fetch_s1_passes.py` → `data/s1_pass_times.json`, per-date/per-platform IW-GRD
   acquisition times + asc/desc, optional `EARTHDATA_TOKEN` Bearer; frames ≤30min
   apart cluster into one pass) > fixed pass windows at Taiwan longitude (ascending
   ≈09:50 UTC, descending ≈21:55 UTC; both tried, best kept). Summary carries
   `s1_real_pass_dates`.
3. **Dynamic gating radius** — gate = base error (SAR geolocation 0.5km + 4wings HIGH
   grid quantization 0.8km + AIS 0.1km) + maneuver uncertainty (speed × Δt × method
   factor), clamped to [1.5, 10] km. Never a fixed radius.
4. **One-to-one assignment** — per (date × pass) group: Hungarian algorithm
   (`scipy.optimize.linear_sum_assignment`) when scipy is available, else globally
   cost-sorted greedy NN with gating. Prevents one vessel "absorbing" several
   detections in dense areas.
5. **Length cross-validation** — when a detection carries a SAR length estimate and the
   AIS side has a registered length: relative diff >35% AND >30m → `length_mismatch`
   (identity-spoofing lead). Dormant until a detection-level SAR source is wired (the
   daily sar-presence dataset carries no lengths).

Output `data/sar_ais_matches.json` (copied to `docs/`, summary embedded in
`data.json` as `sar_ais_matching`):
- `summary` — dark_total / infrastructure_filtered / in_ais_coverage /
  rematched_local / residual_dark / false_dark_removed_pct (SAR window is 30 days but
  local AIS retention is 14–28 days, so out-of-coverage detections are reported
  separately, not counted as verified)
- `rematched` / `residual_dark` / `infrastructure_cells` (recurring-cell centers) detail lists
- `infrastructure` — **per-detection** fixed-infrastructure list (`{lat, lon, date,
  detections, reason}`, `reason` ∈ `recurrence`/`mask`), so every dark-vessel card can
  show a definitive verdict instead of going blank on a filtered point
- `density_grid` — residual-dark 0.1° heatmap cells
- `zone_series` — daily time series by maritime zone (12nm / 24nm contiguous / EEZ,
  via `geofence.classify_maritime_zone`) and by compass sub-zone, both `raw_*` and
  `screened_*` variants — ready for changepoint detection.

Tests: `tests/test_match_sar_ais.py` (pure-function synthetic tests; CI has no scipy,
so the greedy fallback path is exercised there and the Hungarian path when scipy is
installed).

---

## Common Commands
```bash
python3 src/fetch_ais_data.py          # Fetch AIS data + update profiles + save tracks
python3 src/fetch_gfw_data.py          # Fetch GFW SAR data
python3 src/fetch_cloudflare_radar.py  # 網路流量異常偵測 + 海纜旁滯留船隻關聯
python3 src/match_sar_ais.py           # Re-match GFW dark detections vs local AIS
python3 src/detect_ship_transfers.py   # Detect STS rendezvous events
python3 src/analyze_suspicious.py      # Run threat scoring engine
python3 src/generate_dashboard.py      # Consolidate → docs/data.json
python3 src/extract_all_routes.py      # Batch extract vessel routes (tier-1 + tier-2)
python3 src/lookup_itu_mars.py <MMSI>  # Single/batch ITU MARS lookup
python3 src/generate_summary.py --mode daily   # Generate report
python3 src/publish_threads.py --dry-run       # Test Threads post
python3 src/SendMessage.py --dry-run           # Test LINE daily push (text + 2 maps)
python3 src/gov_daily_activity.py -o out.png   # 昨日海警／公務船動態摘要 + 動態圖
```

## Required Secrets (GitHub Actions)
- `GFW_API_TOKEN` — Global Fishing Watch API (required)
- `EARTHDATA_TOKEN` — NASA Earthdata Login user token (optional): passed as Bearer to CMR by `fetch_s1_passes.py` when querying real Sentinel-1 pass times. CMR metadata search also works anonymously, so the pipeline degrades gracefully if unset/expired (EDL tokens expire — regenerate at urs.earthdata.nasa.gov). When CMR fails entirely, `fetch_s1_passes.py` falls back to the CDSE OData catalogue (anonymous)
- `CDSE_ACCESS_KEY` / `CDSE_SECRET_KEY` — Copernicus Data Space (CDSE) S3 keys (optional, **not used by CI**): for the local evidence tool `src/fetch_sar_chip.py`, which pulls a Sentinel-1 GRD image chip around a residual-dark detection via S3 range reads (a few MB, not the 1GB scene) for manual confirmation + rough target-length estimate. Generate at eodata-s3keysmanager.dataspace.copernicus.eu
- `THREADS_USER_ID`, `THREADS_ACCESS_TOKEN`, `THREADS_APP_SECRET` — Threads posting (optional)
- `LINE_CHANNEL_ACCESS_TOKEN`, `LINE_USER_ID` — LINE Bot daily push (`LINEBot.yml` / `SendMessage.py`; optional). Images need the workflow's `GITHUB_TOKEN` (uploaded to `data/charts/` via the Contents API to get public raw URLs)
- `GEMINI_API_KEY` — Google Gemini LLM captions for Threads + LINE daily report (optional; both fall back to fixed templates)
- `CLAUDEFARETOKEN` / `CLAUDEFLAREACCOUNTID` — Cloudflare Radar API（optional，`fetch_cloudflare_radar.py`）。Token 需具備 **Radar Read** 權限；Radar 端點不需要 account ID（只作記錄）。未設定時 `update-data.yml` 的偵測步驟自動跳過。程式亦接受標準名稱 `CLOUDFLARE_API_TOKEN`（注意 secret 名稱是 Cloud**flare**，目前的 `CLAUDEFARE`/`CLAUDEFLARE` 拼法已在別名清單中支援）

> `worker/` 的 secrets **不在 GitHub**，而是用 `wrangler secret put` 設在 Cloudflare 上（見下）。

---

## worker/ — LINE 到工統計助手

與 AIS/SAR 管線完全無關的獨立子系統，只是共用同一個 LINE channel。每個上班日 16:00（TW）在
群組 @全員 詢問**隔一個上班日上午**的到工狀況，20:00 依名冊順序統整後公布，並把當日紀錄存回
`data/attendance/<YYYY-MM>.json`。完整操作手冊在 `worker/README.md`。

**為什麼不是 GitHub Actions**：既有的 `src/SendMessage.py` 是單向推播（只呼叫
`/v2/bot/message/push`）。要**讀取**群組回覆必須有 24h 在線的 HTTPS endpoint 接 LINE webhook，
GitHub Actions 是 cron，收不到。附帶好處是 Cloudflare Cron Triggers 準時，而 GitHub Actions
排程常延遲 5–30 分鐘 —— 對「16:00 打卡提醒」是實質傷害。**不影響** `LINEBot.yml` 的每日海警推播。

**群組 ID 不需人工設定**：bot 被邀進群組時 LINE 送出 `join` 事件，`source.groupId` 直接寫進 KV；
`leave` 時移除。可同時服務多個群組。

**兩個踩過的 LINE API 限制**（改動訊息格式前務必記得）：
- **Quick Reply 在群組不可用** —— 官方文件明載「群組內任何人（含 bot）送出新訊息時 quick reply
  就會消失」，14 人的群組只有第一個人點得到。因此用 **Flex Message + postback 按鈕**：按鈕在訊息
  氣泡內、永久留在對話記錄，每人各自點，postback 直接帶 `source.userId`，零文字解析誤判。
- **@全員** 只能用 `textV2` 的 `substitution` + `{"type":"mention","mentionee":{"type":"all"}}`，且
  **僅限群組／多人聊天室**。`broadcastAsk()` 在收到非 2xx 時自動改用不含 mention 的純文字重送。
- **取得群組成員 userId 清單** 需認證／付費帳號，本案刻意不依賴 —— 改用 `data/attendance_roster.json`
  人工名冊（`/我是`、`/名冊` 兩個群組指令協助建檔）。

**檔案分工**：`src/attendance.js` 是**純函式**核心（日期、狀態解析、名冊配對、清冊格式化），
不 import 任何 Worker/KV/LINE 的東西，所以 `node --test` 能零依賴直接測（CI 有跑）；
`src/line.js` API client + 驗簽 + 訊息建構；`src/storage.js` KV；`src/github.js` Contents API
存檔（沿用 `src/publish_threads.py:_upload_single_file_to_github` 的 GET-sha → PUT-base64 流程）。

**資料**：KV 是即時狀態（`groups` / `openDate` / `day:<iso>` / `members:<gid>` / `roster:cache`），
repo 內的 JSON 是永久存檔。名冊從 raw.githubusercontent 讀 `main`，KV 快取 24h ——
**改名冊只要 commit，不用重新部署**。名冊 schema 由 `tests/test_attendance_roster.py` 在既有的
pytest CI 把關（打錯的 userId 不會有錯誤訊息，那個人只會每天被列成「未回報」）。

**Cloudflare secrets**（`wrangler secret put`，**不是** GitHub secrets）：
`LINE_CHANNEL_ACCESS_TOKEN`（可與現有那把共用）、`LINE_CHANNEL_SECRET`（webhook 驗簽，新的）、
`GITHUB_TOKEN`（fine-grained PAT，只給本 repo `contents: write`）、`ADMIN_KEY`（`/admin/*` 端點）。
既有的 `CLAUDEFARETOKEN` 是 Radar Read 權限，**不能**用來部署 Worker。

**已知限制**：只跳過週六日，國定假日不處理（連假前一天仍會發問）。當日紀錄是單一 KV 鍵的
read-modify-write，KV 無 CAS，同一秒內的兩筆回報理論上可能覆蓋彼此（14 人分散 4 小時，實務上
碰不到；要根治得改成一人一鍵或 Durable Object）。

---

## Architecture Notes
- No build step. Frontend is plain static files.
- AIS data fetched via SOCKS5 proxy (configured in workflow env vars).
- CSIS methodology from "Signals in the Swarm" report: cable proximity, zigzag detection, going-dark, identity manipulation.
- Monitoring area (`TAIWAN_BBOX` in `fetch_ais_data.py`): 19-30°N, 116-130°E (Taiwan Strait, East Taiwan, South/East China Sea).
- Timestamps in ISO 8601 (UTC). Track points deduplicated by consecutive identical lat/lon.
- Static matplotlib maps (gov-vessel tracks, LINE daily 海警 map, suspicious-vessel track map) share their basemap via `src/map_basemap.py`: real coastlines from the committed `data/land_basemap.geojson` (Natural Earth 1:10m clipped to the monitoring area by `build_land_basemap.py`) + submarine cables from `cable-geo.json`. Frontend Leaflet maps are unaffected — they use tile layers.
- Any workflow that renders a matplotlib map with Chinese text must `apt-get install fonts-wqy-zenhei` — GitHub runners ship no CJK font and the labels silently become tofu boxes (`update-data.yml`, `LINEBot.yml` both do).
- Mobile-first design with `@media (max-width: 900px)` breakpoint; safe-area-inset for notched devices.
- z-index stack: bottom-nav 1500, bottom-sheet 1400, map overlays 1000, onboarding 9999.
- MIT License.
