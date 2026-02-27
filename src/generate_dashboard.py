#!/usr/bin/env python3
"""
================================================================================
Dashboard 資料生成腳本
Generate dashboard-ready data from vessel monitoring
================================================================================
"""

import json
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path

DATA_DIR = Path("data")
DOCS_DIR = Path("docs")
DOCS_DIR.mkdir(exist_ok=True)

def main():
    print("📊 生成 Dashboard 資料...")

    # 讀取 GFW vessel 資料
    vessel_path = DATA_DIR / 'vessel_data.json'
    if vessel_path.exists():
        with open(vessel_path, 'r', encoding='utf-8') as f:
            vessel_data = json.load(f)
    else:
        print("⚠️ 找不到 vessel_data.json，跳過")
        vessel_data = {'daily': [], 'summary': {}}

    # 讀取 CSIS 可疑船隻分析結果
    suspicious_path = DATA_DIR / 'suspicious_vessels.json'
    if suspicious_path.exists():
        with open(suspicious_path, 'r', encoding='utf-8') as f:
            suspicious_data = json.load(f)
        print(f"🔍 已載入可疑船隻分析: {suspicious_data.get('summary', {}).get('suspicious_count', 0)} 艘可疑")
    else:
        print("⚠️ 找不到 suspicious_vessels.json，跳過")
        suspicious_data = None

    # 讀取暗船偵測資料
    dark_vessels_path = DATA_DIR / 'dark_vessels.json'
    dark_vessels_data = None
    if dark_vessels_path.exists():
        with open(dark_vessels_path, 'r', encoding='utf-8') as f:
            dark_vessels_data = json.load(f)
        overall = dark_vessels_data.get('overall', {})
        print(f"🔦 已載入暗船資料: {overall.get('dark_vessels', 0)} 艘暗船 / "
              f"{overall.get('total_detections', 0)} 總偵測 "
              f"({overall.get('dark_ratio', 0)}%)")
    else:
        print("⚠️ 找不到 dark_vessels.json，跳過")

    # 讀取 AIS 快照資料（由 fetch_ais_data.py 產生）
    ais_path = DATA_DIR / 'ais_snapshot.json'
    ais_snapshot = None
    if ais_path.exists():
        try:
            with open(ais_path, 'r', encoding='utf-8') as f:
                ais_raw = json.load(f)
            ais_snapshot = {
                'updated_at': ais_raw.get('updated_at', ''),
                'ais_data': ais_raw.get('statistics', {}),
                'vessels': ais_raw.get('vessels', [])[:100]
            }
            print(f"📡 已載入 AIS 快照: {len(ais_snapshot['vessels'])} 艘船")
        except (json.JSONDecodeError, IOError) as e:
            print(f"⚠️ 讀取 ais_snapshot.json 失敗: {e}")
    else:
        print("⚠️ 找不到 ais_snapshot.json，跳過")

    # 讀取軍演預測分析結果
    prediction_path = DATA_DIR / 'exercise_prediction.json'
    prediction_data = None
    if prediction_path.exists():
        try:
            with open(prediction_path, 'r', encoding='utf-8') as f:
                prediction_data = json.load(f)
            status = prediction_data.get('status', 'unknown')
            print(f"📈 已載入軍演預測分析: status={status}")
        except (json.JSONDecodeError, IOError) as e:
            print(f"⚠️ 讀取 exercise_prediction.json 失敗: {e}")
    else:
        print("⚠️ 找不到 exercise_prediction.json，跳過")

    # 讀取身分變更事件（由 fetch_ais_data.py 產生）
    identity_path = DATA_DIR / 'identity_events.json'
    identity_events_data = None
    if identity_path.exists():
        try:
            with open(identity_path, 'r', encoding='utf-8') as f:
                all_events = json.load(f)

            now = datetime.now(timezone.utc)
            cutoff_24h = now - timedelta(hours=24)
            cutoff_7d = now - timedelta(days=7)

            events_24h = []
            events_7d = []
            for ev in all_events:
                try:
                    ts = datetime.fromisoformat(ev['timestamp'].replace('Z', '+00:00'))
                except (ValueError, KeyError):
                    continue
                if ts >= cutoff_7d:
                    events_7d.append(ev)
                    if ts >= cutoff_24h:
                        events_24h.append(ev)

            mmsi_24h = set(ev['mmsi'] for ev in events_24h)
            mmsi_7d = set(ev['mmsi'] for ev in events_7d)

            identity_events_data = {
                'events_24h': events_24h[:50],
                'events_7d': events_7d[:100],
                'summary': {
                    'count_24h': len(events_24h),
                    'count_7d': len(events_7d),
                    'vessels_24h': len(mmsi_24h),
                    'vessels_7d': len(mmsi_7d),
                },
            }
            print(f"🔄 已載入身分變更事件: 24h={len(events_24h)}, 7d={len(events_7d)}")
        except (json.JSONDecodeError, IOError) as e:
            print(f"⚠️ 讀取 identity_events.json 失敗: {e}")
    else:
        print("⚠️ 找不到 identity_events.json，跳過")

    output_path = DOCS_DIR / 'data.json'

    # 合併所有資料
    dashboard = {
        'updated_at': datetime.now(timezone.utc).isoformat() + 'Z',
        'vessel_monitoring': vessel_data,
        'suspicious_analysis': suspicious_data,
        'dark_vessels': dark_vessels_data,
        'exercise_prediction': prediction_data,
        'ais_snapshot': ais_snapshot or {'updated_at': '', 'ais_data': {}, 'vessels': []},
        'identity_events': identity_events_data,
        'status': 'operational',
        'version': '3.2.0'
    }

    # 儲存至 docs 目錄（供 GitHub Pages 使用）
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(dashboard, f, ensure_ascii=False, indent=2)

    print(f"✅ Dashboard 資料已儲存: {output_path}")

    # 複製暗船動畫資料至 docs（獨立檔案，避免主 data.json 過大）
    weekly_dark_path = DATA_DIR / 'weekly_dark_vessels.json'
    if weekly_dark_path.exists():
        shutil.copy2(weekly_dark_path, DOCS_DIR / 'weekly_dark_vessels.json')
        print(f"🎬 已複製暗船動畫資料至 docs/weekly_dark_vessels.json")

    # 複製身分變更事件至 docs（供身分追蹤頁面使用）
    if identity_path.exists():
        shutil.copy2(identity_path, DOCS_DIR / 'identity_events.json')
        print(f"🔄 已複製身分變更事件至 docs/identity_events.json")


if __name__ == "__main__":
    main()
