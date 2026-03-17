#!/usr/bin/env python3
"""
Batch extract vessel routes from ais_track_history.json.
Produces one JSON file per vessel in docs/vessel_routes/.
Only vessels with ≥2 distinct positions get a file.
Cleans up stale files for vessels no longer in history.

Usage: python extract_all_routes.py
"""
import json
import os
import glob


def main():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # Find track history file
    history_path = os.path.join(base, 'docs', 'ais_track_history.json')
    if not os.path.exists(history_path):
        history_path = os.path.join(base, 'data', 'ais_track_history.json')
    if not os.path.exists(history_path):
        print('ais_track_history.json not found')
        return

    print(f'Reading {history_path}...')
    with open(history_path) as f:
        data = json.load(f)

    # Single pass: collect all vessels
    all_vessels = {}  # mmsi -> { name, type, track: [points] }

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
            rec = all_vessels[mmsi]
            # Update name/type if we get a better value
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

    print(f'Found {len(all_vessels)} unique vessels in history')

    # Deduplicate and write
    out_dir = os.path.join(base, 'docs', 'vessel_routes')
    os.makedirs(out_dir, exist_ok=True)
    written_mmsis = set()

    for mmsi, info in all_vessels.items():
        track = info['track']
        if len(track) < 2:
            continue

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
