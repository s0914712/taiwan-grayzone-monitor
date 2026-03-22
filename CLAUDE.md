# CLAUDE.md — Taiwan Gray Zone Monitor

## Overview
Real-time OSINT monitoring of Taiwan's gray zone maritime activity. Integrates AIS vessel data, GFW SAR satellite imagery, and CSIS threat methodology. Static site on GitHub Pages with Python data pipelines automated via GitHub Actions.

## Directory Structure
- `docs/` — Frontend (GitHub Pages root). HTML, CSS, JS, and JSON data files
- `src/` — Python data pipeline scripts (fetch, analyze, generate)
- `data/` — Working/intermediate data (not served to frontend)
- `.github/workflows/` — 3 CI workflows (AIS every 2h, full pipeline every 12h, Threads weekly)

## Tech Stack
- **Frontend:** Vanilla HTML/CSS/JS, Leaflet 1.9.4 (maps), Chart.js 4.4.0 (charts)
- **Backend:** Python 3.11 (pandas, requests, scipy, matplotlib)
- **Hosting:** GitHub Pages (zero-build, static)
- **APIs:** Taiwan Port Bureau (AIS), Global Fishing Watch (SAR), Threads Graph API

## Data Flow
```
GitHub Actions → src/fetch_ais_data.py (AIS via SOCKS5 proxy)
              → src/fetch_gfw_data.py (SAR dark vessels)
              → src/analyze_suspicious.py (CSIS threat scoring)
              → src/exercise_prediction.py (PLA sortie correlation)
              → src/extract_all_routes.py (per-vessel route JSONs)
              → src/generate_dashboard.py (consolidate → docs/data.json)
              → GitHub Pages deploy
```

## Key Frontend Files
| File | Purpose |
|------|---------|
| `docs/index.html` | Main monitoring dashboard with live map |
| `docs/dark-vessels.html` | SAR dark vessel analysis |
| `docs/statistics.html` | Historical statistics & charts |
| `docs/identity-history.html` | AIS identity change timeline |
| `docs/weekly-animation.html` | Dark vessel 90-day animation |
| `docs/ais-animation.html` | AIS track animation |
| `docs/cn-fishing-animation.html` | Chinese fishing vessel animation |
| `docs/js/app.js` | Main controller (~809 LOC) |
| `docs/js/map.js` | Leaflet map module (~1185 LOC) |
| `docs/js/charts.js` | Chart.js integration (~345 LOC) |
| `docs/js/i18n.js` | Internationalization ZH/EN (~457 LOC) |
| `docs/css/main.css` | Single stylesheet with CSS variables |

## Key Conventions

### Vessel Markers
MarineTraffic-style **triangle SVG with notch** (h × 0.7). Dynamically sized and rotated by heading.

### Color Scheme (CSS variables, dark theme)
```css
--bg-primary: #0a0f1c;   --accent-cyan: #00f5ff;
--bg-secondary: #141e32; --accent-orange: #ff6b35;
--bg-card: rgba(25,35,60,0.95); --accent-red: #ff3366;
--text-primary: #e8eef7; --accent-green: #00ff88;
--text-secondary: #8aa4c8; --accent-yellow: #ffd700;
```

### Vessel Type Colors (map.js)
`fishing: #00ff88` `cargo: #00f5ff` `tanker: #ff6b35` `lng: #f0e130` `other: #ff3366` `unknown: #888888`

### i18n
- HTML: `<span data-i18n="key">中文</span>`
- JS: `i18n.t('key')`
- Auto-detect browser lang; toggle ZH↔EN; saved in localStorage

### Animation Pages
Each animation HTML page has **inline JS** (not shared modules). Each has its own Leaflet map + Chart.js + playback controls.

## Key Data Files
| File | Location | Description |
|------|----------|-------------|
| `data.json` | docs/ | Main consolidated dataset for frontend |
| `ais_history.json` | docs/ | 90-day AIS snapshots (1,080 entries) |
| `ais_track_history.json` | docs/ | 14-day full track history (~45MB) |
| `identity_events.json` | docs/ | AIS identity changes (max 5,000) |
| `weekly_dark_vessels.json` | docs/ | 90-day SAR detections for animation |
| `cable_status.json` | docs/ | Submarine cable status |
| `taiwan_cables.json` | docs/ | Cable route GeoJSON |
| `vessel_routes/*.json` | docs/ | 1,000+ per-vessel route files (by MMSI) |

## Common Commands
```bash
python3 src/fetch_ais_data.py          # Fetch AIS data
python3 src/fetch_gfw_data.py          # Fetch GFW SAR data
python3 src/analyze_suspicious.py      # Run CSIS threat analysis
python3 src/generate_dashboard.py      # Consolidate → docs/data.json
python3 src/extract_all_routes.py      # Batch extract vessel routes
python3 src/generate_summary.py --mode daily   # Generate report
python3 src/publish_threads.py --dry-run       # Test Threads post
```

## Required Secrets (GitHub Actions)
- `GFW_API_TOKEN` — Global Fishing Watch API (required)
- `THREADS_USER_ID`, `THREADS_ACCESS_TOKEN`, `THREADS_APP_SECRET` — Threads posting (optional)
- `ANTHROPIC_API_KEY` — LLM captions for Threads (optional)

## Architecture Notes
- No build step. Frontend is plain static files.
- AIS data fetched via SOCKS5 proxy (configured in workflow env vars).
- CSIS methodology from "Signals in the Swarm" report: cable proximity, zigzag detection, going-dark, identity manipulation.
- Monitoring area: ~20-28°N, 112-128°E (Taiwan Strait, East Taiwan, South/East China Sea).
- Timestamps in ISO 8601 (UTC). Track points deduplicated by consecutive identical lat/lon.
- MIT License.
