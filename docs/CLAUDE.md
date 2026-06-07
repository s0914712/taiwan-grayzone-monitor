# docs/ — Frontend (GitHub Pages)

## Architecture
Zero-build static site. All HTML/CSS/JS served directly by GitHub Pages. No framework, no bundler.

## Pages

| File | Purpose | Data Source |
|------|---------|-------------|
| `index.html` | Main monitoring dashboard — live map, vessel markers, suspicious list, onboarding tour | `data.json` |
| `dark-vessels.html` | SAR dark vessel analysis charts + map | `data.json` (dark_vessels section) |
| `statistics.html` | Historical trend charts (vessel counts, dark vessels, fishing effort) | `ais_history.json` |
| `identity-history.html` | AIS identity change timeline table | `identity_events.json` |
| `ais-animation.html` | AIS track playback animation + gray-zone events, focus narrative, AOI/tripwire, going-dark, export. Nav label "軌跡動畫" points here. | `ais_track_history.json`, `data.json`, `ship_transfers.json`, `identity_events.json` |
| `cn-fishing-animation.html` | Chinese fishing vessel animation | `ais_track_history.json` |
| `ship-transfers.html` | STS rendezvous detection results table + map | `ship_transfers.json` |
| `intro.html` | Project introduction / about page | Static |

## JavaScript Modules (`docs/js/`)

| File | LOC | Responsibility |
|------|-----|----------------|
| `app.js` | ~830 | Main controller: init, data loading, sidebar, bottom sheet, mobile nav, vessel list, suspicious list. Entry: `document.addEventListener('DOMContentLoaded', App.init)` |
| `map.js` | ~1390 | Leaflet map: vessel rendering (zoom-based clusters vs detail), suspicious vessel markers, layer toggles, fishing hotspots, submarine cables, route loading. Exports `MapModule`. |
| `charts.js` | ~345 | Chart.js: AIS stats pie chart, overlay cards, trend charts. Exports `ChartsModule`. |
| `i18n.js` | ~490 | Translation dict + auto-detect + toggle. Keys: `namespace.key` (e.g., `nav.grayzone`, `ob.t1`). `localStorage('lang')`. Fires `langchange` CustomEvent. |
| `mobile-nav.js` | ~50 | Mobile hamburger menu toggle |

## CSS (`docs/css/main.css`)

### Design System
```css
--bg-primary: #0a0f1c;   --accent-cyan: #00f5ff;
--bg-secondary: #141e32; --accent-orange: #ff6b35;
--bg-card: rgba(25,35,60,0.95); --accent-red: #ff3366;
--text-primary: #e8eef7; --accent-green: #00ff88;
--text-secondary: #8aa4c8; --accent-yellow: #ffd700;
```

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

### Onboarding Tour
- 5-card carousel, first visit only
- `localStorage('onboarding-seen')` tracks completion
- Inline IIFE in `index.html` after app.js
- CSS: `.onboarding-overlay`, `.onboarding-card`
- Responds to `langchange` event for live language switch

### Animation Pages
Each animation HTML page has **self-contained inline JS** (not shared modules). Each creates its own Leaflet map + playback controls.

## Data Files in docs/

| File | Description | Updated By |
|------|-------------|------------|
| `data.json` | Main consolidated dataset (AIS snapshot + suspicious analysis + dark vessels + predictions) | `generate_dashboard.py` |
| `ais_history.json` | 90-day AIS snapshots (max 1,080 entries) | `fetch_ais_data.py` |
| `ais_track_history.json` | Tier-1: 28-day CN fishing + suspicious tracks | `fetch_ais_data.py` |
| `ais_track_commercial.json` | Tier-2: 28-day cargo/tanker/LNG tracks | `fetch_ais_data.py` |
| `identity_events.json` | AIS identity change events (max 5,000) | `fetch_ais_data.py` |
| `weekly_dark_vessels.json` | 90-day SAR detections for animation | `fetch_weekly_dark_vessels.py` |
| `ship_transfers.json` | STS rendezvous events | `detect_ship_transfers.py` |
| `cable_status.json` | Submarine cable status | Manual |
| `taiwan_cables.json` | Cable route GeoJSON. Feature `properties`: `slug` (fault-match key, must stay in sync with `fetch_cable_status.py` `CABLE_NAME_TO_SLUG`), `color` (hex, no `#`), plus optional metadata rendered in the map popup: `name`, `status`, `cable_type`, `length`, `rfs`, `owners`, `tw_landings`, `cn_landings`. Planned cables (`status` 規劃中) render dashed. | Manual |
| `vessel_routes/{mmsi}.json` | Per-vessel route files (1,000+) | `extract_all_routes.py` |
| `cn_gov_vessel_tracks.png` | Combined 14-day track map of China gov / special-interest vessels (海警/海巡/海救/科研), colored by sub-category | `plot_gov_vessel_tracks.py` |
