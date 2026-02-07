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
    
    # 讀取 vessel 資料
    vessel_path = DATA_DIR / 'vessel_data.json'
    if vessel_path.exists():
        with open(vessel_path, 'r', encoding='utf-8') as f:
            vessel_data = json.load(f)
    else:
        print("⚠️ 找不到 vessel_data.json，跳過")
        vessel_data = {'daily': [], 'summary': {}}
    
    # 生成 dashboard 資料
    dashboard = {
        'updated_at': datetime.utcnow().isoformat() + 'Z',
        'vessel_monitoring': vessel_data,
        'status': 'operational',
        'version': '1.0.0'
    }
    
    # 儲存至 docs 目錄（供 GitHub Pages 使用）
    output_path = DOCS_DIR / 'data.json'
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(dashboard, f, ensure_ascii=False, indent=2)
    
    print(f"✅ Dashboard 資料已儲存: {output_path}")


if __name__ == "__main__":
    main()
