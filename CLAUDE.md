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
- `.github/workflows/` — 3 CI workflows (AIS every 2h, full pipeline every 12h incl. once-daily 00:00 UTC gov-vessel track map, Threads weekly)

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

On match `type_name` is overridden to the category and a `gov_type` field (+ `is_coast_guard` for the coastguard sub-type) is set; tier-1 tracks gain a `gov:<category>` flag and these vessels are always retained in tier-1 (so routes accumulate). MMSI-prefix matching is deliberately **not** used (block `413875xxx` is shared with civilian vessels). Research keywords use word boundaries (`\b`) to avoid false hits (e.g. `AN TONG JING TANG` must not match `TONG JI`). Taiwan CGA (海巡署) is intentionally excluded. `plot_gov_vessel_tracks.py` renders combined historical tracks (colored by category) to `docs/cn_gov_vessel_tracks.png`.

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
