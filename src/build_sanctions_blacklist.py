#!/usr/bin/env python3
"""制裁油輪黑名單轉換器 — CSV → analyze_suspicious 可讀 JSON。

輸入 `data/sanctions_blacklist.csv`（多機構制裁油輪名單：欄位
name,imo,built,class,flag + 各制裁機構欄位存列名日期或空白）
→ 輸出 `data/sanctions_blacklist.json`。

這是 UN 1718 清單（27 艘）之外的**第二個制裁來源**，涵蓋
OFAC/UK FCDO/UANI/EU/瑞士 SECO/紐西蘭 MFAT 等對影子船隊的指定。
analyze_suspicious 以 **IMO 精確命中**採信（+8），不以船名比對此清單
（1422 筆含大量常見船名，純名稱易撞名）。

更新名單：換掉 CSV 後重跑 `python3 src/build_sanctions_blacklist.py`。
"""
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
CSV_FILE = _REPO / "data" / "sanctions_blacklist.csv"
OUT_FILE = _REPO / "data" / "sanctions_blacklist.json"

# CSV 制裁機構欄 → 顯示用代碼
PROGRAM_LABELS = {
    'ofac': 'OFAC',      # 美國財政部 OFAC
    'fcdo': 'UK-FCDO',   # 英國外交部
    'uani': 'UANI',      # United Against Nuclear Iran
    'eu': 'EU',          # 歐盟
    'aso': 'ASO',        # (Australia)
    'gac': 'GAC',
    'un': 'UN',
    'seco': 'SECO',      # 瑞士 SECO
    'mfat': 'MFAT',      # 紐西蘭 MFAT
}


def build():
    if not CSV_FILE.exists():
        raise SystemExit(f"找不到來源 CSV: {CSV_FILE}")

    vessels = []
    with open(CSV_FILE, encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            imo = (row.get('imo') or '').strip()
            name = (row.get('name') or '').strip()
            if not name:
                continue
            programs = {}
            for col, label in PROGRAM_LABELS.items():
                date = (row.get(col) or '').strip()
                if date:
                    programs[label] = date
            vessels.append({
                'name': name,
                'imo': imo,
                'flag': (row.get('flag') or '').strip(),
                'built': (row.get('built') or '').strip(),
                'class': (row.get('class') or '').strip(),
                'programs': sorted(programs.keys()),
                'program_dates': programs,
            })

    with_imo = sum(1 for v in vessels if v['imo'])
    out = {
        'source': 'Maritime sanctions blacklist (multi-agency shadow-fleet tanker list)',
        'source_note': 'OFAC / UK FCDO / UANI / EU / SECO / MFAT / UN designations',
        'last_updated': datetime.now(timezone.utc).date().isoformat(),
        'description': (
            'Tankers designated by one or more sanctions authorities. '
            'Matched by IMO only in analyze_suspicious.py (name matching '
            'disabled — too many common names collide over 1400+ vessels).'),
        'vessel_count': len(vessels),
        'vessels': vessels,
    }
    OUT_FILE.write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f"✅ 寫入 {OUT_FILE}: {len(vessels)} 艘 ({with_imo} 艘有 IMO)")


if __name__ == '__main__':
    build()
