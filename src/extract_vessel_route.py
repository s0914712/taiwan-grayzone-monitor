#!/usr/bin/env python3
"""
Extract vessel route history from ais_track_history.json.
Usage: python extract_vessel_route.py <MMSI> [--name NAME] [--imo IMO] [--flag FLAG] [--type TYPE]
"""
import json
import argparse
import os

def main():
    parser = argparse.ArgumentParser(description='Extract vessel route from AIS track history')
    parser.add_argument('mmsi', help='MMSI of the vessel to extract')
    parser.add_argument('--name', default='', help='Vessel name')
    parser.add_argument('--imo', default='', help='IMO number')
    parser.add_argument('--flag', default='', help='Flag state')
    parser.add_argument('--type', default='', help='Vessel type')
    args = parser.parse_args()

    # Find track history file
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    history_path = os.path.join(base, 'docs', 'ais_track_history.json')
    if not os.path.exists(history_path):
        history_path = os.path.join(base, 'data', 'ais_track_history.json')

    print(f'Reading {history_path}...')
    with open(history_path) as f:
        data = json.load(f)

    track = []
    vessel_name = args.name
    vessel_type = args.type

    for entry in data:
        ts = entry.get('timestamp', '')
        for v in entry.get('vessels', []):
            if v.get('mmsi') == args.mmsi:
                if not vessel_name and v.get('name'):
                    vessel_name = v['name']
                if not vessel_type and v.get('type_name'):
                    vessel_type = v['type_name']
                track.append({
                    't': ts,
                    'lat': v.get('lat'),
                    'lon': v.get('lon'),
                    'speed': v.get('speed', 0),
                    'heading': v.get('heading', 0)
                })

    if not track:
        print(f'No data found for MMSI {args.mmsi}')
        return

    # Deduplicate by position (skip consecutive identical lat/lon)
    deduped = [track[0]]
    for p in track[1:]:
        if p['lat'] != deduped[-1]['lat'] or p['lon'] != deduped[-1]['lon']:
            deduped.append(p)
        elif p != track[-1]:  # Always keep last point
            continue
        else:
            deduped.append(p)

    output = {
        'mmsi': args.mmsi,
        'name': vessel_name,
        'imo': args.imo,
        'flag': args.flag,
        'type': vessel_type,
        'track': deduped
    }

    out_dir = os.path.join(base, 'docs', 'vessel_routes')
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f'{args.mmsi}.json')

    with open(out_path, 'w') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f'Extracted {len(deduped)} track points (from {len(track)} total) -> {out_path}')

if __name__ == '__main__':
    main()
