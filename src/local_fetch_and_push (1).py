#!/usr/bin/env python3
"""
本機排程版：抓 AIS 資料 → 自動 commit & push 到 GitHub
適用於航港局封鎖雲端 IP 的情況，需在台灣本地網路執行。

用法:
  1. 單次執行:  python local_fetch_and_push.py
  2. 排程執行:  python local_fetch_and_push.py --schedule 30   (每30分鐘)
  
前置需求:
  - pip install requests
  - git remote 已設好 (ssh 或 https token)
  - 在你的 repo 根目錄下執行
"""

import os
import sys
import json
import time
import subprocess
import argparse
import requests
import urllib3
from datetime import datetime, timezone
from collections import defaultdict

# 航港局 SSL 憑證缺少 Subject Key Identifier，停用驗證
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============================================================
# 設定
# ============================================================
def find_git_root():
    """從腳本位置向上尋找 .git 目錄"""
    path = os.path.dirname(os.path.abspath(__file__))
    for _ in range(5):  # 最多往上找 5 層
        if os.path.isdir(os.path.join(path, '.git')):
            return path
        path = os.path.dirname(path)
    # 找不到就用腳本所在目錄
    return os.path.dirname(os.path.abspath(__file__))

REPO_DIR = find_git_root()
DATA_DIR = os.path.join(REPO_DIR, 'data')
DOCS_DIR = os.path.join(REPO_DIR, 'docs')
OUTPUT_FILE = os.path.join(DATA_DIR, 'ais_snapshot.json')
HISTORY_FILE = os.path.join(DATA_DIR, 'vessel_history.json')
DASHBOARD_FILE = os.path.join(DOCS_DIR, 'data.json')

MPB_URL = "https://mpbais.motcmpb.gov.tw/aismpb/tools/geojsonais.ashx"
MPB_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "en-US,en;q=0.9,zh;q=0.8,zh-TW;q=0.7",
    "Referer": "https://mpbais.motcmpb.gov.tw/aismpb/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
}

TAIWAN_BBOX = {'lat_min': 20, 'lat_max': 28, 'lon_min': 112, 'lon_max': 128}

DRILL_ZONES = {
    'north': {'name': '北區', 'bounds': [[25.5, 121.0], [26.8, 122.5]]},
    'east':  {'name': '東區', 'bounds': [[23.0, 122.5], [25.5, 125.0]]},
    'south': {'name': '南區', 'bounds': [[21.5, 119.0], [23.0, 121.0]]},
    'west':  {'name': '西區', 'bounds': [[23.5, 118.5], [25.0, 120.0]]},
}

FISHING_HOTSPOTS = {
    'taiwan_bank':   {'name': '台灣灘漁場',   'bounds': [[22.0, 117.0], [23.5, 119.5]]},
    'penghu':        {'name': '澎湖漁場',     'bounds': [[23.0, 119.0], [24.0, 120.0]]},
    'kuroshio_east': {'name': '東部黑潮漁場', 'bounds': [[22.5, 121.0], [24.5, 122.0]]},
    'northeast':     {'name': '東北漁場',     'bounds': [[24.8, 121.5], [25.8, 123.0]]},
    'southwest':     {'name': '西南沿岸漁場', 'bounds': [[22.0, 120.0], [23.0, 120.8]]},
}


# ============================================================
# 工具函式
# ============================================================

def is_in_zone(lat, lon, bounds):
    return (bounds[0][0] <= lat <= bounds[1][0] and
            bounds[0][1] <= lon <= bounds[1][1])

def classify_vessel_type(type_code):
    if type_code is None:
        return 'unknown'
    t = int(type_code)
    if 30 <= t <= 39: return 'fishing'
    elif t == 35:     return 'military'
    elif 40 <= t <= 49: return 'high_speed'
    elif 50 <= t <= 59: return 'special'
    elif 60 <= t <= 69: return 'passenger'
    elif 70 <= t <= 79: return 'cargo'
    elif 80 <= t <= 89: return 'tanker'
    elif t == 0:        return 'unknown'
    else:               return 'other'


# ============================================================
# 收集
# ============================================================

def collect_ais_data():
    print(f"  📡 正在擷取航港局 AIS 資料...")
    try:
        resp = requests.get(MPB_URL, headers=MPB_HEADERS, timeout=30, verify=False)
        resp.raise_for_status()
        geojson = resp.json()
    except requests.RequestException as e:
        print(f"  ❌ 請求失敗: {e}")
        return {}

    features = geojson.get("features", [])
    print(f"  HTTP {resp.status_code} | {len(resp.content):,} bytes | {len(features)} features")

    if not features:
        print("  ⚠️ 回傳 0 features，可能被封鎖或伺服器異常")
        return {}

    vessels = {}
    for feat in features:
        props = feat.get("properties", {})
        coords = feat.get("geometry", {}).get("coordinates", [None, None])
        lon = coords[0] if coords and len(coords) > 0 else None
        lat = coords[1] if coords and len(coords) > 1 else None
        if lon is None or lat is None:
            continue
        if not (TAIWAN_BBOX['lat_min'] <= lat <= TAIWAN_BBOX['lat_max'] and
                TAIWAN_BBOX['lon_min'] <= lon <= TAIWAN_BBOX['lon_max']):
            continue

        mmsi = str(props.get("MMSI", "")).strip()
        if not mmsi or mmsi == "0":
            continue

        type_code = props.get("Ship_and_Cargo_Type")
        type_name = classify_vessel_type(type_code)
        drill_zone = next((zid for zid, z in DRILL_ZONES.items()
                           if is_in_zone(lat, lon, z['bounds'])), None)
        fishing_hotspot = next((hid for hid, h in FISHING_HOTSPOTS.items()
                                if is_in_zone(lat, lon, h['bounds'])), None)
        suspicious = (type_name == 'fishing' and
                      drill_zone is not None and
                      fishing_hotspot is None)

        vessels[mmsi] = {
            'mmsi': mmsi,
            'name': str(props.get("ShipName", "")).strip() or f'MMSI-{mmsi}',
            'imo': str(props.get("IMO_Number", "")).strip(),
            'call_sign': str(props.get("Call_Sign", "")).strip(),
            'lat': lat, 'lon': lon,
            'type': type_code,
            'type_name': type_name,
            'speed': float(props.get("SOG", 0) or 0),
            'heading': float(props.get("COG", 0) or 0),
            'nav_status': str(props.get("Navigational_Status", "")),
            'in_drill_zone': drill_zone,
            'in_fishing_hotspot': fishing_hotspot,
            'suspicious': suspicious,
            'record_time': props.get("Record_Time", ""),
            'last_update': datetime.now(timezone.utc).isoformat(),
        }

    print(f"  ✅ 有效船舶: {len(vessels)}")
    return vessels


# ============================================================
# 分析
# ============================================================

def analyze_data(vessels):
    stats = {
        'total_vessels': len(vessels),
        'fishing_vessels': sum(1 for v in vessels.values() if v['type_name'] == 'fishing'),
        'suspicious_count': 0,
        'avg_speed': 0.0,
        'by_type': defaultdict(int),
        'in_drill_zones': {k: 0 for k in DRILL_ZONES},
        'in_fishing_hotspots': {k: 0 for k in FISHING_HOTSPOTS},
    }
    if not vessels:
        stats['by_type'] = {}
        return stats

    total_speed = 0
    for v in vessels.values():
        stats['by_type'][v['type_name']] += 1
        if v['in_drill_zone']:
            stats['in_drill_zones'][v['in_drill_zone']] += 1
        if v['in_fishing_hotspot']:
            stats['in_fishing_hotspots'][v['in_fishing_hotspot']] += 1
        if v['suspicious']:
            stats['suspicious_count'] += 1
        total_speed += v['speed']

    stats['avg_speed'] = round(total_speed / len(vessels), 2)
    stats['by_type'] = dict(stats['by_type'])
    return stats


# ============================================================
# 儲存
# ============================================================

def save_all(vessels, stats):
    now_str = datetime.now(timezone.utc).isoformat()
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(DOCS_DIR, exist_ok=True)

    vessel_list = list(vessels.values())

    # 1. 快照
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump({
            'updated_at': now_str,
            'source': 'MPB_geojsonais',
            'statistics': stats,
            'vessels': vessel_list,
        }, f, ensure_ascii=False, indent=2)

    # 2. 歷史
    history = []
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                history = json.load(f)
        except Exception:
            pass

    history.append({
        'timestamp': now_str,
        'total_vessels': stats['total_vessels'],
        'fishing_vessels': stats['fishing_vessels'],
        'suspicious_count': stats['suspicious_count'],
        'by_type': stats['by_type'],
        'in_drill_zones': stats['in_drill_zones'],
    })
    history = history[-1000:]
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    # 3. Dashboard
    existing = {}
    if os.path.exists(DASHBOARD_FILE):
        try:
            with open(DASHBOARD_FILE, 'r', encoding='utf-8') as f:
                existing = json.load(f)
        except Exception:
            pass

    existing['updated_at'] = now_str
    existing['ais_snapshot'] = {
        'updated_at': now_str,
        'source': 'MPB_geojsonais',
        'ais_data': stats,
        'vessels': vessel_list[:100],
    }
    with open(DASHBOARD_FILE, 'w', encoding='utf-8') as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    print(f"  📄 {OUTPUT_FILE} ({len(vessel_list)} 艘)")
    print(f"  📊 {DASHBOARD_FILE}")


# ============================================================
# Git push
# ============================================================

def git_push():
    """自動 commit & push 到 GitHub"""
    now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

    try:
        os.chdir(REPO_DIR)

        # 檢查是否是 git repo
        result = subprocess.run(['git', 'rev-parse', '--is-inside-work-tree'],
                                capture_output=True, text=True)
        if result.returncode != 0:
            print("  ⚠️ 不在 git repo 內，跳過 push")
            return False

        subprocess.run(['git', 'add', 'data/', 'docs/'], check=True,
                       capture_output=True)

        # 檢查有沒有變更
        result = subprocess.run(['git', 'diff', '--cached', '--quiet'],
                                capture_output=True)
        if result.returncode == 0:
            print("  ℹ️ 無資料變更，跳過 commit")
            return True

        subprocess.run(
            ['git', 'commit', '-m', f'📡 AIS snapshot update {now_str}'],
            check=True, capture_output=True
        )
        subprocess.run(['git', 'push'], check=True, capture_output=True)
        print(f"  🚀 已推送到 GitHub ({now_str})")
        return True

    except subprocess.CalledProcessError as e:
        print(f"  ❌ Git 操作失敗: {e}")
        print(f"     stdout: {e.stdout}")
        print(f"     stderr: {e.stderr}")
        return False


# ============================================================
# 主程式
# ============================================================

def run_once():
    """執行一次完整的 抓取→分析→儲存→推送 流程"""
    print(f"\n{'='*50}")
    print(f"  {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"  Repo: {REPO_DIR}")
    print(f"{'='*50}")

    vessels = collect_ais_data()
    if not vessels:
        print("  ⚠️ 無資料，跳過此輪")
        return

    stats = analyze_data(vessels)
    save_all(vessels, stats)
    git_push()

    print(f"  ✅ 船舶: {stats['total_vessels']} | "
          f"漁船: {stats['fishing_vessels']} | "
          f"可疑: {stats['suspicious_count']}")


def main():
    parser = argparse.ArgumentParser(description="本機 AIS 抓取 + 自動推 GitHub")
    parser.add_argument('--schedule', type=int, default=0,
                        help='排程間隔 (分鐘)，0 = 只跑一次')
    args = parser.parse_args()

    if args.schedule <= 0:
        run_once()
    else:
        print(f"🔄 排程模式：每 {args.schedule} 分鐘執行一次 (Ctrl+C 停止)")
        while True:
            run_once()
            print(f"\n  ⏳ 下次執行: {args.schedule} 分鐘後...")
            try:
                time.sleep(args.schedule * 60)
            except KeyboardInterrupt:
                print("\n已停止。")
                break


if __name__ == '__main__':
    main()
