# src/ — Python Data Pipeline

## Pipeline Execution Order
```
fetch_ais_data.py → fetch_gfw_data.py → detect_ship_transfers.py
→ analyze_suspicious.py → exercise_prediction.py
→ extract_all_routes.py → generate_dashboard.py
```

## Files

### Core Pipeline (executed by GitHub Actions)

| File | Purpose | Input | Output |
|------|---------|-------|--------|
| `fetch_ais_data.py` | Fetch AIS from Taiwan Port Bureau (SOCKS5 proxy), update profiles, save tier-1/tier-2 tracks, detect identity changes | Port Bureau API | `data/ais_snapshot.json`, `docs/vessel_profiles.json`, `docs/ais_track_history.json`, `docs/ais_track_commercial.json`, `data/identity_events.json` |
| `fetch_gfw_data.py` | Fetch GFW SAR satellite detections for dark vessels + fishing hotspots | GFW API (`GFW_API_TOKEN`) | `dark_vessels.json` |
| `fetch_weekly_dark_vessels.py` | Extract 90-day SAR dark vessel data grouped by date | GFW API | `weekly_dark_vessels.json` |
| `detect_ship_transfers.py` | Detect ship-to-ship rendezvous (<10m, >1hr), classify as pair trawling vs suspicious | `ais_track_history.json`, `ais_snapshot.json` | `ship_transfers.json` |
| `analyze_suspicious.py` | **Core threat scoring engine** — see root CLAUDE.md for full scoring docs | profiles, tracks (tier-1+2), cables, identity events, sanctions, MARS cache, STS transfers | `suspicious_vessels.json` |
| `exercise_prediction.py` | Correlate dark vessel activity with PLA sortie data (Granger causality) | `dark_vessels.json`, PLA sortie data | `exercise_prediction.json` |
| `extract_all_routes.py` | Batch extract per-vessel route JSONs from tier-1+tier-2 track history | `ais_track_history.json`, `ais_track_commercial.json` | `docs/vessel_routes/{mmsi}.json` |
| `generate_dashboard.py` | Consolidate all data → single JSON for frontend; copy auxiliary files to docs/. Also refreshes `vessel_monitoring.daily` dark-vessel trend from `dark_vessels.json` and accumulates it in `data/dark_vessel_history.json` (persistent, max 365 days) so the trend never freezes. | All `data/*.json` | `docs/data.json` + copies to docs/, `data/dark_vessel_history.json` |

### Utilities

| File | Purpose |
|------|---------|
| `lookup_itu_mars.py` | ITU MARS ship station registry scraper. CLI: `python3 lookup_itu_mars.py <MMSI>`. Batch mode with 2s rate limit. Cache in `data/itu_mars_cache.json` (30-day expiry). |
| `extract_vessel_route.py` | CLI utility to extract single vessel route by MMSI |
| `plot_gov_vessel_tracks.py` | Scan `docs/vessel_routes/` for China gov / special-interest vessels (海警/海巡/海救/科研) and render their combined 14-day tracks to a dark-themed PNG, colored by sub-category, with place-name labels + per-track vessel name/position. CLI: `python3 plot_gov_vessel_tracks.py [-o out.png]` (default `docs/cn_gov_vessel_tracks.png`). Requires matplotlib (CJK labels need WenQuanYi/Noto, else fall back to DejaVu). **Runs once daily (00:00 UTC) in `update-data.yml`** — committed & deployed with the rest of `docs/`. |
| `generate_summary.py` | Generate daily/weekly text summary reports |
| `publish_threads.py` | Generate charts + maps, publish to Threads (requires `THREADS_*` secrets) |

## Key Data Structures

### Vessel Profile (`vessel_profiles.json`)
```python
{
    "412345678": {
        "mmsi": "412345678",
        "names_seen": ["SHIP A", "SHIP B"],
        "types_seen": ["fishing", "cargo"],
        "total_snapshots": 42,
        "fishing_hotspot_snapshots": 15,
        "last_seen_timestamps": ["2024-01-01T00:00:00+00:00", ...],  # last 50
        "last_imo": "9876543",
        "last_callsign": "BXYZ",
    }
}
```

### Track Entry (both tier-1 and tier-2)
```python
{
    "timestamp": "2024-01-01T00:00:00+00:00",
    "vessel_count": 150,
    "vessels": [
        {"mmsi": "412345678", "name": "SHIP A", "lat": 24.5, "lon": 121.0,
         "speed": 8.5, "heading": 180.0, "type_name": "fishing"}
    ]
}
```

### Track Tiers
- **Tier-1** (`docs/ais_track_history.json`): CN fishing + suspicious vessels, max 168 entries (14 days). Used by analyze_suspicious / detect_ship_transfers / route extraction / per-MMSI route lookups; kept compact (rounded coords, no per-vessel `suspicious` flag). Suspicion is resolved frontend-side from `data.json` (`suspicious_analysis`).
- **Animation subset** (`docs/ais_track_animation.json`): last 7 days of tier-1, written alongside it by `fetch_ais_data.py` (`AIS_TRACK_ANIMATION_DAYS = 7`). The animation pages fetch this first and fall back to the full tier-1 file — roughly halves the animation download.
- **Tier-2** (`docs/ais_track_commercial.json`): cargo/tanker/LNG + identity-changed, max 336 entries (28 days)
- Identity-changed MMSIs loaded from `data/identity_events.json` (last 7 days)
- Large accumulating files (`vessel_profiles.json`, `ais_track_history.json`, `ais_track_commercial.json`) are written to `docs/` as compact JSON (no indent) to stay under GitHub's 100 MiB per-file limit. `data/` counterparts are gitignored.

## Conventions
- All timestamps ISO 8601 UTC
- MMSI as strings (preserve leading zeros)
- Monitoring bbox: lat 20-28°N, lon 112-128°E
- Track points deduplicated by consecutive identical lat/lon
- `classify_vessel_type(code)` maps AIS type codes → `fishing`/`cargo`/`tanker`/`lng`/`other`/`unknown`
- China gov / special-interest vessel detection: `classify_gov_vessel(name)` in `fetch_ais_data.py` returns a sub-category by **name keyword only** — `coastguard` 海警 / `msa` 海巡(海事局) / `rescue` 海救(救助局) / `research` 科研·情報船. MMSI-prefix matching was rejected — block `413875xxx` is shared with civilian vessels (e.g. HUAHANG10DP). On match, `type_name` is overridden to the category (fixes AIS mis-reporting these as fishing/special/other), `gov_type` is set on the snapshot vessel (plus `is_coast_guard` for the coastguard sub-type); tier-1 tracks gain a compact `gov:<category>` flag and these vessels are always retained in tier-1 (so routes accumulate). Research keywords use word boundaries to avoid false hits (e.g. `AN TONG JING TANG` ≠ `TONG JI`). `is_coast_guard_vessel()` is kept as a thin compat wrapper. Taiwan CGA (海巡署) is intentionally excluded.
