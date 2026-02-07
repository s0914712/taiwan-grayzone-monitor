#!/usr/bin/env python3
"""
================================================================================
Dashboard 資料生成腳本
Generate dashboard-ready data from vessel monitoring
================================================================================
"""

import json
from datetime import datetime
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

    # 讀取現有 data.json（保留 AIS snapshot 資料）
    output_path = DOCS_DIR / 'data.json'
    existing_data = {}
    if output_path.exists():
        try:
            with open(output_path, 'r', encoding='utf-8') as f:
                existing_data = json.load(f)
        except (json.JSONDecodeError, IOError):
            existing_data = {}

    # 合併所有資料
    dashboard = {
        'updated_at': datetime.utcnow().isoformat() + 'Z',
        'vessel_monitoring': vessel_data,
        'suspicious_analysis': suspicious_data,
        'status': 'operational',
        'version': '2.0.0'
    }

    # 保留 AIS snapshot 資料（由 fetch_ais_data.py 寫入）
    if 'ais_snapshot' in existing_data:
        dashboard['ais_snapshot'] = existing_data['ais_snapshot']

    # 儲存至 docs 目錄（供 GitHub Pages 使用）
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(dashboard, f, ensure_ascii=False, indent=2)

    print(f"✅ Dashboard 資料已儲存: {output_path}")


if __name__ == "__main__":
    main()
