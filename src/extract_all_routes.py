#!/usr/bin/env python3
"""
Batch extract vessel routes from track history files.
Reads both tier-1 (ais_track_history.json) and tier-2 (ais_track_commercial.json).
Produces one JSON file per vessel in docs/vessel_routes/.
Only vessels with ≥2 distinct positions get a file.
Cleans up stale files for vessels no longer in history.

Usage: python extract_all_routes.py
"""
import json
import os
import glob


def load_track_file(path, all_vessels):
    """Load a track history JSON and accumulate vessel data."""
    if not os.path.exists(path):
        return 0
    print(f'  Reading {path}...')
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, list):
        return 0

    count = 0
    for entry in data:
        ts = entry.get('timestamp', '')
        for v in entry.get('vessels', []):
            mmsi = v.get('mmsi')
            if not mmsi:
                continue
            if mmsi not in all_vessels:
                all_vessels[mmsi] = {
                    'name': v.get('name', ''),
                    'type': v.get('type_name', ''),
                    'track': []
                }
                count += 1
            rec = all_vessels[mmsi]
            if not rec['name'] and v.get('name'):
                rec['name'] = v['name']
            if not rec['type'] and v.get('type_name'):
                rec['type'] = v['type_name']
            rec['track'].append({
                't': ts,
                'lat': v.get('lat'),
                'lon': v.get('lon'),
                'speed': v.get('speed', 0),
                'heading': v.get('heading', 0)
            })
    return count


def main():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # Track history files to read (tier-1 + tier-2)
    track_files = [
        # Tier 1: CN fishing + suspicious (prefer docs/ copy, fallback data/)
        os.path.join(base, 'docs', 'ais_track_history.json'),
        os.path.join(base, 'data', 'ais_track_history.json'),
        # Tier 2: cargo, tanker, LNG, identity-changed
        os.path.join(base, 'docs', 'ais_track_commercial.json'),
        os.path.join(base, 'data', 'ais_track_commercial.json'),
    ]

    all_vessels = {}  # mmsi -> { name, type, track: [points] }

    # Deduplicate paths: for each base name, prefer docs/ over data/
    seen_basenames = set()
    for path in track_files:
        basename = os.path.basename(path)
        if basename in seen_basenames:
            if not os.path.exists(path):
                continue
            # data/ fallback: only load if docs/ version didn't exist
            continue
        if os.path.exists(path):
            seen_basenames.add(basename)
            load_track_file(path, all_vessels)
        # If docs/ doesn't exist, try data/ fallback on next iteration

    # Retry: load data/ versions for files not found in docs/
    for path in track_files:
        basename = os.path.basename(path)
        if basename not in seen_basenames and os.path.exists(path):
            seen_basenames.add(basename)
            load_track_file(path, all_vessels)

    print(f'Found {len(all_vessels)} unique vessels across all track files')

    # Sort tracks by timestamp and deduplicate
    out_dir = os.path.join(base, 'docs', 'vessel_routes')
    os.makedirs(out_dir, exist_ok=True)
    written_mmsis = set()

    for mmsi, info in all_vessels.items():
        track = info['track']
        if len(track) < 2:
            continue

        # Sort by timestamp
        track.sort(key=lambda p: p.get('t', ''))

        # Dedup: skip consecutive identical lat/lon, always keep last point
        deduped = [track[0]]
        for i, p in enumerate(track[1:], 1):
            if p['lat'] != deduped[-1]['lat'] or p['lon'] != deduped[-1]['lon']:
                deduped.append(p)
            elif i == len(track) - 1:
                deduped.append(p)

        if len(deduped) < 2:
            continue

        output = {
            'mmsi': mmsi,
            'name': info['name'],
            'imo': '',
            'flag': '',
            'type': info['type'],
            'track': deduped
        }

        out_path = os.path.join(out_dir, f'{mmsi}.json')
        with open(out_path, 'w') as f:
            json.dump(output, f, ensure_ascii=False, separators=(',', ':'))
        written_mmsis.add(mmsi)

    # Clean stale files
    removed = 0
    for path in glob.glob(os.path.join(out_dir, '*.json')):
        stem = os.path.splitext(os.path.basename(path))[0]
        if stem not in written_mmsis:
            os.remove(path)
            removed += 1

    print(f'Wrote {len(written_mmsis)} route files, removed {removed} stale files')


if __name__ == '__main__':
    main()
