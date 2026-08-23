#!/usr/bin/env python3
"""
Batch extract vessel routes from track history files.
Reads both tier-1 (ais_track_history.json) and tier-2 (ais_track_commercial.json).
Produces one JSON file per vessel in data/vessel_routes/.
Only vessels with ≥2 distinct positions get a file.
Cleans up stale files for vessels no longer in history.

When SUPABASE_URL + SUPABASE_SERVICE_KEY are set the same routes are also
upserted into the Supabase `vessel_routes` table, which is what the frontend
reads (the local files remain for offline tooling and as the fallback).

Usage: python extract_all_routes.py [--no-supabase]
"""
import argparse
import json
import os
import glob
from datetime import datetime, timezone

import supabase_store
from io_utils import atomic_write_json


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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-supabase', action='store_true',
                        help='只寫本地檔案，跳過 Supabase 上傳')
    args = parser.parse_args()

    # 先取時間戳：本輪之後所有被 upsert 的列 updated_at 都會 >= 這個值，
    # 早於它的就是已離開保留窗口的船（見 supabase_store._delete_stale）。
    run_started_at = datetime.now(timezone.utc).isoformat()

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # Tier-1 (docs/): frontend fetches it. Tier-2 (data/): pipeline-only, Actions cache.
    track_files = [
        os.path.join(base, 'docs', 'ais_track_history.json'),
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
    out_dir = os.path.join(base, 'data', 'vessel_routes')
    os.makedirs(out_dir, exist_ok=True)
    written_mmsis = set()
    rows = []

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
        atomic_write_json(out_path, output, compact=True)
        written_mmsis.add(mmsi)
        rows.append(supabase_store.route_row(
            mmsi, info['name'], '', '', info['type'], deduped))

    # Clean stale files
    removed = 0
    for path in glob.glob(os.path.join(out_dir, '*.json')):
        stem = os.path.splitext(os.path.basename(path))[0]
        if stem not in written_mmsis:
            os.remove(path)
            removed += 1

    print(f'Wrote {len(written_mmsis)} route files, removed {removed} stale files')

    push_to_supabase(rows, run_started_at, skip=args.no_supabase)


def push_to_supabase(rows, run_started_at, skip=False):
    """把航跡列 upsert 到 Supabase；未設定或失敗時只警告，不中斷 pipeline。

    上傳失敗不該讓整條 AIS pipeline 失敗——本地檔案與 vessel-data 分支仍是
    可用的後備來源，前端也會自動退回它們。
    """
    if skip:
        print('  ⏭️  --no-supabase：跳過上傳')
        return
    if not supabase_store.is_configured(write=True):
        print('  ⏭️  SUPABASE_URL / SUPABASE_SERVICE_KEY 未設定，跳過上傳')
        return
    try:
        upserted, deleted = supabase_store.upsert_routes(
            rows, run_started_at=run_started_at)
        print(f'  ☁️  Supabase vessel_routes: upsert {upserted} 列, '
              f'清除過期 {deleted} 列')
    except Exception as e:
        print(f'  ⚠️ Supabase 上傳失敗（保留本地檔案作為後備）: {e}')


if __name__ == '__main__':
    main()
