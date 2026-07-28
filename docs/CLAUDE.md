# docs/ — Frontend (GitHub Pages)

## Architecture
Zero-build static site. All HTML/CSS/JS served directly by GitHub Pages. No framework, no bundler.

## Pages

| File | Purpose | Data Source |
|------|---------|-------------|
| `index.html` | Main monitoring dashboard — live map, vessel markers, suspicious list, onboarding tour. Sidebar is deliberately down to **three threat-first sections**: suspicious (count lives in the section title as `#suspiciousCount`, the old map overlay card was removed) → gov vessels → cable status → update-info. The AIS 即時統計 / GFW 衛星監測 / AIS 身分變更 / 近期船隻 blocks and the 深度文章 link were **removed to cut information load** — the same numbers live on `statistics.html`, `dark-vessels.html`, `identity-history.html` and the nav "更多" dropdown, and the mobile bottom sheet keeps its own compact 即時統計 tiles. The 12-card bilingual About block moved to `intro.html`; index keeps a one-line `.about-teaser` pointer. | `data.json` |
| `dark-vessels.html` | SAR dark vessel analysis charts + map. Map shows the **SAR×AIS verification result as targets + zones** when `sar_ais_matches.json` is available: verified residual dark (red), outside-coverage unverified (hollow orange), locally-identified false-dark targets (green, popup carries vessel name/MMSI), infrastructure cells (yellow), density heat (off by default), and baseline/12nm/24nm zone lines via `MapBaselineFactory`; toggleable legend, re-renders on `langchange`. Falls back to raw GFW sample dots when the match file is missing. | `data.json` (dark_vessels section), `sar_ais_matches.json` |
| `sar-ais-match.html` | SAR×AIS cross-match report, deliberately slimmed to **three blocks**: 3 stat tiles (residual dark / re-matched locally / false-dark removed %), residual-dark density map (density + rematched + infra layers), and the chip-retrieval worklist. The daily-series/zone charts, false-dark detail table, and method panel were removed (Chart.js no longer loaded) — `zone_series` data still exists in the JSON for anyone who wants it. Standalone Leaflet (no MapModule); bilingual via `lang-zh-only`/`lang-en-only` spans; re-renders on `langchange`. Shows a pending notice until the pipeline first generates the data file. | `sar_ais_matches.json`, `sar_chip_worklist.json` |
| `statistics.html` | Historical trend charts (vessel counts, dark vessels, fishing effort). Panels are grouped under three `.stats-group-title` headings (暗船趨勢與軍演預測 / AIS 船隊歷史 / SCFI 相關性); the two raw-data tables (每日數據, 週資料明細) are collapsed by default via the `.panel-title.collapsible` pattern. The region/flag distribution pies were **removed** (duplicated dark-vessels.html's richer table/list) — a link panel points there instead. | `ais_history.json` |
| `network-traffic.html` | **網路流量異常監控** — Cloudflare Radar 流量／延遲序列（觀測值 vs 季節基線的 Chart.js 折線圖，異常區間以 severity 色帶標示）＋ 異常事件卡（幅度／時數／穩健 z ＋ 異常前 12h 在海纜旁滯留的船隻表）＋ Cloudflare 已知中斷標註 ＋ 4 步驟偵測方法說明。序列以 `.series-tab` 切換。**示範模式**：`cf_radar.json` 不存在／無 `timestamps`（token 未設定或管線還沒跑過）時，`buildDemo()` 產生合成序列（含一次注入的模擬海纜中斷）並顯示醒目的 DEMO 橫幅——絕不能讓示範資料被誤認為真實觀測。JS 在 `js/network-traffic.js`；Chart.js 4.4.0，無 Leaflet。 | `cf_radar.json`（fallback: 內建示範資料）|
| `identity-history.html` | AIS identity change timeline table | `identity_events.json` |
| `ais-animation.html` | AIS track playback animation + gray-zone events, focus narrative, AOI/tripwire, going-dark, export. Nav label "軌跡動畫" points here. | `ais_track_history.json`, `data.json`, `ship_transfers.json`, `identity_events.json` |
| `cn-fishing-animation.html` | Chinese fishing vessel animation | `ais_track_history.json` |
| `ship-transfers.html` | STS rendezvous detection results table + map | `ship_transfers.json` |
| `intro.html` | Project introduction / about page. Hosts the bilingual 12-card About/Methodology sections (`#aboutSection` / `#aboutSectionEn`, moved from index.html) between FAQ and the article grid. | Static |

### Desktop header nav (all data pages + index + blog)
Every page's header shows the 4 primary links (灰色地帶監測 / 暗船偵測 / 統計分析 / 軌跡動畫), the current page's own link if it's a secondary one (kept visible as the `active` pill), and a native no-JS `<details class="nav-more">` "更多/More" dropdown holding the rest (網路流量, SAR×AIS 比對, 大陸漁船, 旁靠偵測, 身分追蹤, 研究報告, 深度文章, 關於本站 — minus the current page). CSS: `.nav-more*` in main.css (z-index 1100, hidden ≤900px where the bottom nav takes over). `research-…` and `intro.html` keep their minimal 2-link headers. When adding a page, add it to the dropdown on the other pages.

## JavaScript Modules (`docs/js/`)

| File | LOC | Responsibility |
|------|-----|----------------|
| `app.js` | ~770 | Main controller: init, data loading, freshness indicator, sidebar, suspicious list, gov-vessel list, cable status. `updateVesselList()` / `updateIdentitySection()` / `timeAgo()` were removed with the index sections they fed. `setupMobileNavigation()` no longer builds the mobile shell — it **injects index-specific bottom-sheet sections** (route search, layer toggles, `bs*` stats/gov/suspicious) via `window.MobileNav.addSheetSection()` (no-op on desktop where the shell doesn't exist). Entry: `document.addEventListener('DOMContentLoaded', App.init)` |
| `map-data.js` | ~480 | Static lookup tables (MID flag table, vessel colors, fishing hotspots, gov regex, FOC MIDs, region colors, territorial basepoints) + pure helpers (`getMidFlag`, `getGovType`, `govLabel`, `createVesselIcon`, `debounce`, `offsetPolygonNm`, `_decodeNavStatus`). Exports `MapData`. |
| `map-baseline.js` | ~180 | Territorial baseline / 12nm / 24nm rendering. Exports `MapBaselineFactory(map, layers)`. |
| `map-vessels.js` | ~920 | Vessel rendering (cluster + detail), dark/suspicious/gov layers, vessel list panel, info cards, FOC filter, sanctions matching. Dark-vessel popups are enriched with a **definitive SAR×AIS verdict** (`sar_ais_matches.json`), resolved in priority order so no card is ever blank: false-dark AIS identity → **fixed infrastructure** (`infrastructure` per-detection list, wind farm / platform, with `reason` recurrence/mask) → residual dark + maritime zone / outside coverage → an `⏳ updating` fallback when the match file is loaded but the point doesn't key-match. Plus darkship chip forensics verdicts + PNG link (`chips/results.json`, fetched via raw.githubusercontent on Pages — same pattern as vessel routes); lookup keyed on date+lat+lon at 2-decimal precision, popup content bound as a function so late-loading data and language switches show on open. Exports `MapVesselsFactory(map, layers)`. |
| `map-routes.js` | ~330 | Route loading (pre-generated + history fallback), polyline rendering, track info panel, map snapshot. Exports `MapRoutesFactory(map, layers)`. |
| `map-cables.js` | ~140 | Submarine cable GeoJSON layer + MODA fault status. Exports `MapCablesFactory(map, layers)`. |
| `map-bathymetry.js` | ~200 | Seascape seabed depth layer (openwatersio/seascape, tiles CC BY 4.0 © Open Water Software, LLC). Seascape serves **Terrarium-encoded raster DEM tiles** (depth per pixel, not pre-shaded imagery), so a custom `L.GridLayer` decodes each 512px tile on a canvas and applies a depth color-relief with a deliberate color break at **200 m** (shelf edge — same contour as scoring criterion 3). Tile URL template / zoom range / attribution are read from the TileJSON (`tiles.openwaters.io/seascape/raster.json`) at runtime, nothing hardcoded; renders in a dedicated `bathymetry` pane (z 250, between basemap and overlays); CC BY attribution control appears only while the layer is on. Off by default, lazy-loaded on first toggle. Vector contour tiles (MVT) exist upstream but would need a vector-tile plugin — not wired. Exports `MapBathymetryFactory(map, layers)`. |
| `map.js` | ~190 | Core: Leaflet init, layer groups, factory instantiation, public `MapModule` API (delegates to sub-modules). **HTML script order: map-data → map-baseline → map-vessels → map-routes → map-cables → map-bathymetry → map.js** (all `defer`). Regression gate: `node tests/map-integration.js` (jsdom + real Leaflet). |
| `charts.js` | ~250 | Chart.js: daily/trend/pie charts, overlay cards, zone counts. Exports `ChartsModule`. `displayGfwStats` / `updateAisStats` / `renderSparkline` were dropped when the index GFW + AIS-stats sidebar sections were removed (nothing else called them). |
| `i18n.js` | ~490 | Translation dict + auto-detect + toggle. Keys: `namespace.key` (e.g., `nav.grayzone`, `ob.t1`). `localStorage('lang')`. Fires `langchange` CustomEvent. |
| `network-traffic.js` | ~380 | `network-traffic.html` 的全部邏輯：載入 `cf_radar.json`／退回示範資料、序列切換、Chart.js 折線圖 + `anomalyBands` 自訂 plugin（把異常區間畫成底色帶）、異常事件卡與候選船隻表、Cloudflare 中斷標註。**異常與基線都是後端算好的，這裡只負責呈現**——唯一的例外是示範模式的合成序列。語言切換時重繪（示範資料的標籤是中英文字串，會整份重建）。 |
| `mobile-nav.js` | ~160 | **Single shared mobile shell for ALL pages incl. index**: bottom nav (5 tabs), 6-entry Anim popover, bottom sheet + overlay + drag-to-dismiss. Bails out at `window.innerWidth > 900`. Exposes `window.MobileNav = { sheet, popover, closeAll, addSheetSection(html) }` so a page can inject sheet sections (injected above the always-last update-info section). Must load **before** `app.js` on index. |

## CSS (`docs/css/main.css`)

### Design System
```css
--bg-primary: #0a0f1c;   --accent-cyan: #00f5ff;
--bg-secondary: #141e32; --accent-orange: #ff6b35;
--bg-card: rgba(25,35,60,0.95); --accent-red: #ff3366;
--text-primary: #e8eef7; --accent-green: #00ff88;
--text-secondary: #8aa4c8; --accent-yellow: #ffd700;
```

### Typography floor (desktop)
`:root` carries `--fs-label: 10px` (micro labels) / `--fs-caption: 11px` / `--fs-body: 12px` (lists, table cells, popups) / `--fs-title: 13px` (section & panel titles). **Nothing may go below 10px** — the old 6–9px sidebar/legend/popup sizes were raised to these tokens; new UI text should reference the tokens, not raw px. The ≤900px mobile block keeps its own 12px+ sizes; the `@media (min-width: 901px)` "desktop readability" block now only bumps numeric *values* (stat numbers), not labels. Desktop sidebar rail is 320px (`.app-container` grid).

### Vessel Type Colors
`fishing: #00ff88` `cargo: #00f5ff` `tanker: #ff6b35` `lng: #f0e130` `coastguard: #ffffff` `msa: #4d9fff` `rescue: #ff9500` `research: #c77dff` `other: #ff3366` `unknown: #888`

China gov / special-interest vessels are marked with a **persistent white circle** (`displayGovVessels` → dedicated `layers.govVessels`, mirroring the high-risk `suspiciousVessels` layer so they survive zoom/pan/cluster — single ships stay visible). Category is shown via the colored triangle icon + popup badge + clickable legend entry: 海警 coastguard (white 🛡️), 海巡 msa (blue ⚓), 海救 rescue (orange 🛟), 科研/情報 research (purple 🔬). `map.js` resolves the category via `getGovType(v)` (exported; backend `gov_type` / `type_name`, falling back to `GOV_REGEX`); `locateVesselType` shows a per-category list panel. The gov layer follows the `vessels` layer toggle. Keep `GOV_REGEX` in sync with `classify_gov_vessel()` in `src/fetch_ais_data.py`.

The index homepage has a dedicated **公務／科研船追蹤** section (`#govVesselSection` sidebar + `#bsGovList` bottom sheet), filled by `updateGovVesselList()` (app.js) which scans the full AIS snapshot (`rawVesselList`) — so gov vessels are listed even when the map is zoomed out into cluster mode (single ships are otherwise invisible). The `lng`/LNG-Gas special marking (glow, badge, legend, list panel) was **removed**; the `VESSEL_COLORS.lng` constant remains but is unused.

### Responsive Breakpoints
- `@media (max-width: 900px)` — Main mobile threshold (sidebar → drawer, bottom sheet appears)
- `@media (max-width: 600px)` — Small screen adjustments
- `@media (max-width: 400px)` — Very small screens

### z-index Stack
```
9999  onboarding overlay
2000  sidebar (mobile)
1999  sidebar-overlay
1500  mobile bottom nav
1499  nav popover
1400  bottom sheet
1000  route search, map overlays
```

### Mobile Design
- Bottom nav: 5 tabs (Monitor, Dark, Stats, Anim, Tools)
- Bottom sheet: drag-up panel with vessel stats + suspicious list
- `env(safe-area-inset-bottom)` for notched devices

## Key Patterns

### Vessel Marker
MarineTraffic-style triangle SVG with notch. Size/rotation from heading. Created by `createVesselIcon(color, isSuspicious, heading)` in `map.js`.

### Data Loading (app.js)
```
App.init() → MapModule.init('map') → setupMobileNavigation()
           → loadData() → fetch('data.json')
                        → MapModule.renderVesselsForZoom()
                        → displaySuspiciousVessels()
                        → updateIdentitySection()
```

### i18n Usage
```html
<span data-i18n="nav.grayzone">灰色地帶監測</span>
<input data-i18n-placeholder="bs.route_search">
```
```javascript
i18n.t('app.mmsi')        // simple
i18n.t('idx.ago_h', 24)   // {0} replacement
```

### Bilingual evergreen articles + `/en/` mirror
`blog-*.html` explainer/pillar/glossary pages carry both languages inline
(`lang-zh-only` / `lang-en-only` + `data-i18n`) and toggle client-side. Their
English URLs under `docs/en/` are **generated** by `src/generate_i18n_pages.py`
(do not hand-edit `docs/en/*.html`). Each article has Article/Breadcrumb/FAQ
JSON-LD (+`mainEntityOfPage`/`speakable`), a visible `.ai-summary` abstract, and
a shared bilingual `.topic-cluster` footer (CSS in `main.css`) inserted before
`<footer>`. Adding an article → see `src/CLAUDE.md` (`generate_i18n_pages.py`).

### Onboarding Tour
- 5-card carousel, first visit only
- `localStorage('onboarding-seen')` tracks completion
- Inline IIFE in `index.html` after app.js
- CSS: `.onboarding-overlay`, `.onboarding-card`
- Responds to `langchange` event for live language switch

### Animation Pages
Animation page logic lives in external scripts `js/ais-animation.js` (~2,100 lines) and `js/cn-fishing-animation.js` (~1,130 lines), loaded with `defer` after `i18n.js` (extracted verbatim from the former inline `<script>` blocks). Each creates its own Leaflet map + playback controls and bootstraps on `DOMContentLoaded`. Regression gate: `node tests/animation-smoke.js` (jsdom + real Leaflet).

## Data Files in docs/

| File | Description | Updated By |
|------|-------------|------------|
| `data.json` | Main consolidated dataset (AIS snapshot + suspicious analysis + dark vessels + predictions) | `generate_dashboard.py` |
| `ais_history.json` | 90-day AIS snapshots (max 1,080 entries) | `fetch_ais_data.py` |
| `ais_track_history.json` | Tier-1: 14-day CN fishing + suspicious tracks (analysis + route lookups) | `fetch_ais_data.py` |
| `ais_track_animation.json` | 7-day subset of tier-1 for the animation pages (fetched first; tier-1 is the fallback). Animation range buttons: 1/3/7 days, default 7. | `fetch_ais_data.py` |
| `ais_track_commercial.json` | Tier-2: 28-day cargo/tanker/LNG tracks | `fetch_ais_data.py` |
| `identity_events.json` | AIS identity change events (max 5,000) | `fetch_ais_data.py` |
| `weekly_dark_vessels.json` | 90-day SAR detections for animation | `fetch_weekly_dark_vessels.py` |
| `ship_transfers.json` | STS rendezvous events | `detect_ship_transfers.py` |
| `sar_ais_matches.json` | SAR×AIS re-match results (summary, rematched/residual/infra lists, density grid, zone series) — copied from `data/` by `generate_dashboard.py` | `match_sar_ais.py` |
| `sar_chip_worklist.json` | Chip-retrieval worklist: east/southwest residual dark × Sentinel-1 coverage, with per-target acquisition time + ready-made `fetch_sar_chip.py` command — copied from `data/` by `generate_dashboard.py`; rendered by the report page's 取證清單 panel | `build_chip_worklist.py` |
| `cf_radar.json` | Cloudflare Radar 流量／延遲時間序列（最近 14 天）+ 季節基線 + 異常事件（含關聯船隻）+ 中斷標註。`data.json` 內的 `network_anomalies` 已剝掉原始陣列，畫圖要用這個檔。**由 `cloudflare-radar.yml` 每 2 小時直接寫入並提交（唯一提交的一份，`data/cf_radar.json` 已 gitignore）** | `fetch_cloudflare_radar.py` |
| `cable_status.json` | Submarine cable status | Manual |
| `taiwan_cables.json` | Cable route GeoJSON. Feature `properties`: `slug` (fault-match key, must stay in sync with `fetch_cable_status.py` `CABLE_NAME_TO_SLUG`), `color` (hex, no `#`), plus optional metadata rendered in the map popup: `name`, `status`, `cable_type`, `length`, `rfs`, `owners`, `tw_landings`, `cn_landings`. Planned cables (`status` 規劃中) render dashed. | Manual |
| `../data/vessel_routes/{mmsi}.json` | Per-vessel route files (27,000+). **Not in docs/ and not tracked on main** — they live on the single-commit `vessel-data` branch (CI regenerates + force-pushes each run); the frontend fetches them from `raw.githubusercontent.com/.../vessel-data/` when on GitHub Pages (see `ROUTE_FILE_BASE` in `map-routes.js` / `ship-transfers.html`), relative `../data/` path otherwise | `extract_all_routes.py` |
| `cn_gov_vessel_tracks.png` | Combined 14-day track map of China gov / special-interest vessels (海警/海巡/海救/科研), colored by sub-category | `plot_gov_vessel_tracks.py` |
| `reports/<date>.{html,json}` + `reports/index.html` | Daily bilingual report page + machine-readable summary. Carries the SAR×AIS 比對成效 funnel and the SAR 取證影像 gallery (verdict badges from `darkship_batch.verdict_key()`); wide tables live in `.rp-table-wrap` (`overflow-x:auto`) so the page never scrolls sideways on mobile | `generate_report.py` |
| `reports/chips/*.jpg` | 520px/q72 thumbnails of the featured SAR chips — the 1.5MB originals stay out of the Pages artifact, so `<img onerror>` falls back to `raw.githubusercontent.com/.../main/chips/*.png`. Rotated out after 14 days by mtime | `generate_report.py` |
