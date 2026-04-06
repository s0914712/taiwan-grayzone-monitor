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
| `weekly-animation.html` | Leaflet-based vessel trail animation (max 300 vessels, 2-layer glow) | `ais_track_history.json` |
| `ais-animation.html` | AIS track playback animation | `ais_track_history.json` |
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
`fishing: #00ff88` `cargo: #00f5ff` `tanker: #ff6b35` `lng: #f0e130` `other: #ff3366` `unknown: #888`

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
Each animation HTML page has **self-contained inline JS** (not shared modules). Each creates its own Leaflet map + playback controls. `weekly-animation.html` uses Leaflet polylines (replaced deck.gl for mobile compatibility).

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
| `taiwan_cables.json` | Cable route GeoJSON | Manual |
| `vessel_routes/{mmsi}.json` | Per-vessel route files (1,000+) | `extract_all_routes.py` |
