# CLAUDE.md — Taiwan Gray Zone Monitor

## Overview
Real-time OSINT monitoring of Taiwan's gray zone maritime activity. Integrates AIS vessel data, GFW SAR satellite imagery, and CSIS threat methodology. Static site on GitHub Pages with Python data pipelines automated via GitHub Actions.

**Sub-directory docs** (auto-loaded when working in those dirs):
- `src/CLAUDE.md` — Python pipeline details, data structures, file-by-file reference
- `docs/CLAUDE.md` — Frontend architecture, JS modules, CSS design system, z-index, i18n

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
              → src/detect_ship_transfers.py (STS rendezvous detection)
              → src/analyze_suspicious.py (threat scoring)
              → src/exercise_prediction.py (PLA sortie correlation)
              → src/extract_all_routes.py (per-vessel route JSONs)
              → src/generate_dashboard.py (consolidate → docs/data.json)
              → GitHub Pages deploy
```

---

## analyze_suspicious.py — Threat Scoring Engine

### Architecture
The engine loads multiple data sources, iterates all known vessels (profile ∪ track), and produces a per-vessel risk classification.

**Data sources loaded:**
1. `vessel_profiles.json` — AIS-observed vessel metadata (names, types, timestamps)
2. `ais_track_history.json` (tier-1) — CN fishing + suspicious vessel tracks (14 days)
3. `ais_track_commercial.json` (tier-2) — cargo/tanker/LNG + identity-changed vessel tracks
4. `cable-geo.json` — Submarine cable route GeoJSON
5. `identity_events.json` — AIS identity change events (7-day window)
6. `un_sanctions_vessels.json` — UN sanctions vessel list
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

### Scoring Criteria (8 criteria)

| # | Criterion | Detection Method | Raw Score |
|---|-----------|-----------------|-----------|
| 1a | Cable Proximity | Track points within 5km of submarine cable (bbox pre-filtered) | +2 |
| 1b | Cable Loitering | Low speed (<8kn) near cable for >3hr (actual timestamps) | +3 |
| 2 | Zigzag Pattern | ≥3 turns of ≥45° heading change (calc_bearing from positions) | +1 |
| 3 | 200m Depth Contour | ≥30% of track time near continental shelf edge | +1 |
| 4 | AIS Anomalies | Name changes ≥2, going dark >18hr gaps, type changes, identity events | +1 (medium) / +3 (high) |
| 5 | Non-Top-10 Flag | MMSI MID not in top-10 flag state set | +1 |
| 6 | UN Sanctions | IMO or name match against sanctions list | +8 |
| 7 | AIS Spoofing | Impossible physics / box pattern / circle pattern | +4 each |
| 8 | ITU MARS Mismatch | Ship name, IMO, or call sign differs from ITU registry | +3 |
| 9 | STS Transfer | Involved in ship-to-ship rendezvous (suspicious: +5, any: +2) | +2/+5 |

### Vessel Type Multiplier
Applied to **behavioral scores only** (criteria 1-3, 5). High-threat indicators (4, 6-9) are NOT multiplied.

| Type | Multiplier | Rationale |
|------|-----------|-----------|
| cargo, tanker, lng | ×1.0 | Long anchor chains, high tonnage → real cable damage risk |
| fishing | ×0.2 | Small, routine operations, low cable threat |
| other, unknown | ×0.5 | Uncertain |

### Combo Bonuses (also multiplied by vessel type)
- Cable proximity + zigzag: +3 (possible anchor dragging)
- Cable proximity + loitering: +2

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
- Teleportation: calculated speed > 100 km/h between consecutive points
- Speed mismatch: calc_speed / reported_SOG ratio > 3× or < 0.33×
- Bearing mismatch: calc_bearing vs reported COG differs > 60°
- Skips going-dark gaps (>18h) to avoid false positives

**Box Pattern** (`check_box_pattern`):
- ≥3 near-90° turns (65°-115° tolerance)
- Path closed (start-end < 5km) or bounding box < 5km
- Filters stationary points (speed < 0.5kn)

**Circle Pattern** (`check_circle_pattern`):
- Centroid-based radius CV < 0.25 (low variation = symmetric)
- Arc coverage > 270° (near-complete circle)
- Radius range: 0.1-5.0 km (excludes GPS drift and normal sailing)

### Output
- `data/suspicious_vessels.json` — Top 50 suspicious vessels + top 200 classifications + full summary stats
- Summary includes per-criterion trigger counts, risk distribution, exclusion breakdown

### Performance Optimization
- **Exclusion early return**: Buoys/beacons skip all expensive analysis
- **Cable bbox pre-filter**: Only cables whose bounding box overlaps vessel track are checked
- Cable proximity: O(track_points × nearby_cable_segments) instead of all cables

---

## Common Commands
```bash
python3 src/fetch_ais_data.py          # Fetch AIS data + update profiles + save tracks
python3 src/fetch_gfw_data.py          # Fetch GFW SAR data
python3 src/detect_ship_transfers.py   # Detect STS rendezvous events
python3 src/analyze_suspicious.py      # Run threat scoring engine
python3 src/generate_dashboard.py      # Consolidate → docs/data.json
python3 src/extract_all_routes.py      # Batch extract vessel routes (tier-1 + tier-2)
python3 src/lookup_itu_mars.py <MMSI>  # Single/batch ITU MARS lookup
python3 src/generate_summary.py --mode daily   # Generate report
python3 src/publish_threads.py --dry-run       # Test Threads post
```

## Required Secrets (GitHub Actions)
- `GFW_API_TOKEN` — Global Fishing Watch API (required)
- `THREADS_USER_ID`, `THREADS_ACCESS_TOKEN`, `THREADS_APP_SECRET` — Threads posting (optional)
- `GEMINI_API_KEY` — Google Gemini LLM captions for Threads (optional)

## Architecture Notes
- No build step. Frontend is plain static files.
- AIS data fetched via SOCKS5 proxy (configured in workflow env vars).
- CSIS methodology from "Signals in the Swarm" report: cable proximity, zigzag detection, going-dark, identity manipulation.
- Monitoring area: ~20-28°N, 112-128°E (Taiwan Strait, East Taiwan, South/East China Sea).
- Timestamps in ISO 8601 (UTC). Track points deduplicated by consecutive identical lat/lon.
- Mobile-first design with `@media (max-width: 900px)` breakpoint; safe-area-inset for notched devices.
- z-index stack: sidebar 2000, sidebar-overlay 1999, bottom-nav 1500, popover 1499, bottom-sheet 1400, onboarding 9999.
- MIT License.
