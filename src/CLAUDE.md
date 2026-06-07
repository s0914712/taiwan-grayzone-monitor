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
| `plot_coast_guard_tracks.py` | Scan `docs/vessel_routes/` for (China) Coast Guard vessels and render their combined 14-day tracks to a dark-themed PNG. CLI: `python3 plot_coast_guard_tracks.py [-o out.png]` (default `docs/coast_guard_tracks.png`). Requires matplotlib. |
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
- **Tier-1** (`docs/ais_track_history.json`): CN fishing + suspicious vessels, max 168 entries (14 days). Served to the frontend animation (max display range 14 days); kept compact (rounded coords, no per-vessel `suspicious` flag) to avoid mobile load/parse failures. Suspicion is resolved frontend-side from `data.json` (`suspicious_analysis`).
- **Tier-2** (`docs/ais_track_commercial.json`): cargo/tanker/LNG + identity-changed, max 336 entries (28 days)
- Identity-changed MMSIs loaded from `data/identity_events.json` (last 7 days)
- Large accumulating files (`vessel_profiles.json`, `ais_track_history.json`, `ais_track_commercial.json`) are written to `docs/` as compact JSON (no indent) to stay under GitHub's 100 MiB per-file limit. `data/` counterparts are gitignored.

## Conventions
- All timestamps ISO 8601 UTC
- MMSI as strings (preserve leading zeros)
- Monitoring bbox: lat 20-28°N, lon 112-128°E
- Track points deduplicated by consecutive identical lat/lon
- `classify_vessel_type(code)` maps AIS type codes → `fishing`/`cargo`/`tanker`/`lng`/`other`/`unknown`
- Coast Guard detection: `is_coast_guard_vessel(name, mmsi)` in `fetch_ais_data.py` flags (China) Coast Guard ships by **name keyword only** (`COAST GUARD`, `CCG\d*`, `HAIJING`, `海警`, `CHINA COAST`). MMSI-prefix matching was rejected — block `413875xxx` is shared with civilian vessels (e.g. HUAHANG10DP). On match, `type_name` is overridden to `coastguard` (fixes AIS mis-reporting CCG ships as fishing/other) and a `is_coast_guard` bool is set on the snapshot vessel; tier-1 tracks gain a compact `cg:1` flag and CCG vessels are always retained in tier-1 (so routes accumulate). Taiwan CGA (海巡) is intentionally excluded to avoid mislabeling friendly vessels.
