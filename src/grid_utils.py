"""共用格網統計工具 — 徘徊熱區的 0.1° 分箱。

僅依賴 stdlib。cell 取整方式與 match_sar_ais.build_density_grid 一致
（round(x / cell) * cell），未來可讓該處委派到這裡（現階段不動 working code）。
"""
from collections import defaultdict


def grid_cell(lat, lon, cell_deg=0.1):
    """回傳 (lat, lon) 所屬格網中心座標 tuple。"""
    return (round(lat / cell_deg) * cell_deg,
            round(lon / cell_deg) * cell_deg)


def build_stat_grid(events, cell_deg=0.1):
    """徘徊事件 → 格網統計（純函式）。

    events: [{'lat', 'lon', 'mmsi', 'hours', 'avg_speed_kn'}, ...]
      hours 缺值視為 0；avg_speed_kn 為 None 時不列入該格均速。
    回傳 [{'lat', 'lon', 'events', 'vessels', 'loiter_hours', 'avg_speed_kn'}]
      依 loiter_hours 降冪。vessels = 不重複 MMSI 數；avg_speed_kn 以
      各事件時數加權（無可用速度時為 None）。
    """
    cells = defaultdict(lambda: {
        'events': 0, 'mmsis': set(),
        'hours': 0.0, 'speed_weighted': 0.0, 'speed_hours': 0.0})
    for ev in events:
        lat = ev.get('lat')
        lon = ev.get('lon')
        if lat is None or lon is None:
            continue
        cell = cells[grid_cell(lat, lon, cell_deg)]
        hours = ev.get('hours') or 0.0
        cell['events'] += 1
        if ev.get('mmsi'):
            cell['mmsis'].add(ev['mmsi'])
        cell['hours'] += hours
        speed = ev.get('avg_speed_kn')
        if isinstance(speed, (int, float)) and hours > 0:
            cell['speed_weighted'] += speed * hours
            cell['speed_hours'] += hours

    out = []
    for (clat, clon), c in cells.items():
        out.append({
            'lat': round(clat, 4),
            'lon': round(clon, 4),
            'events': c['events'],
            'vessels': len(c['mmsis']),
            'loiter_hours': round(c['hours'], 1),
            'avg_speed_kn': (round(c['speed_weighted'] / c['speed_hours'], 1)
                             if c['speed_hours'] > 0 else None),
        })
    out.sort(key=lambda r: (-r['loiter_hours'], -r['events'], r['lat'], r['lon']))
    return out
