#!/usr/bin/env python3
"""
================================================================================
CSIS 灰色地帶可疑船隻分析引擎
Suspicious Vessel Analysis based on CSIS "Signals in the Swarm" Methodology
================================================================================

方法論參考：
  CSIS Futures Lab - "Signals in the Swarm: The Data Behind China's
  Maritime Gray Zone Campaign Near Taiwan" (October 2025)

偵測邏輯：
  1. 行為比例門檻 (Behavioral Proportion Threshold)
     - 漁撈熱點停留 <10% → 掛漁船旗但不在漁場
  2. AIS 異常偵測 (AIS Anomaly Detection)
     - Going Dark：船隻消失後重新出現
     - 變更船名：同一 MMSI 使用多個船名
     - 變更類型：船型資訊前後不一致
================================================================================
"""

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

DATA_DIR = Path("data")
HISTORY_FILE = DATA_DIR / "vessel_profiles.json"
OUTPUT_FILE = DATA_DIR / "suspicious_vessels.json"
IDENTITY_EVENTS_FILE = DATA_DIR / "identity_events.json"

# CSIS 門檻設定
BEHAVIORAL_FISHING_RATIO = 0.10      # <10% 時間在漁撈熱點
SNAPSHOT_INTERVAL_HOURS = 6          # 每 6 小時一次快照
NAME_CHANGE_THRESHOLD = 2            # 船名變更次數 >= 2 為異常
GOING_DARK_GAP_HOURS = 18            # 超過 18 小時未出現視為 going dark


def load_vessel_history():
    """載入累積的船隻行為 profile（按 MMSI 分組的 dict）"""
    if not HISTORY_FILE.exists():
        print("⚠️ 找不到 vessel_profiles.json，跳過分析")
        return {}

    with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if isinstance(data, dict):
        return data

    # 向後相容：如果是舊的 list 格式則回傳空 dict
    print("⚠️ vessel_profiles.json 格式不符（非 dict），跳過分析")
    return {}


def load_identity_events():
    """載入身分變更事件紀錄，按 MMSI 分組，僅保留近 7 天"""
    if not IDENTITY_EVENTS_FILE.exists():
        return {}

    try:
        with open(IDENTITY_EVENTS_FILE, 'r', encoding='utf-8') as f:
            events = json.load(f)
    except Exception:
        return {}

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    by_mmsi = {}
    for ev in events:
        try:
            ts = datetime.fromisoformat(ev['timestamp'].replace('Z', '+00:00'))
            if ts < cutoff:
                continue
        except (ValueError, KeyError):
            continue
        mmsi = ev.get('mmsi', '')
        if mmsi:
            by_mmsi.setdefault(mmsi, []).append(ev)

    return by_mmsi


def analyze_behavioral_threshold(profile):
    """
    行為比例門檻分析 (CSIS Criterion 1)
    漁船不到 10% 時間在漁場 → 掛漁船旗但不在漁場，可疑
    """
    total = profile['total_snapshots']
    if total < 2:
        return False, {}

    fishing_ratio = profile.get('fishing_hotspot_snapshots', 0) / total

    triggered = fishing_ratio < BEHAVIORAL_FISHING_RATIO

    return triggered, {
        'fishing_hotspot_ratio': round(fishing_ratio, 3),
        'threshold': f'<{BEHAVIORAL_FISHING_RATIO:.0%} fishing'
    }


def analyze_ais_anomalies(profile, identity_events=None):
    """
    AIS 異常偵測 (CSIS Criterion 2)
    - 多次變更船名
    - Going dark（AIS 訊號消失再出現）
    - 身分變更事件（來自 identity tracking）
    """
    anomalies = []

    # 船名變更偵測
    name_count = len(profile.get('names_seen', []))
    if name_count >= NAME_CHANGE_THRESHOLD:
        anomalies.append({
            'type': 'name_change',
            'description': f'使用 {name_count} 個不同船名',
            'names': profile['names_seen'],
            'severity': 'high' if name_count >= 5 else 'medium'
        })

    # Going dark 偵測（分析快照間的時間間隔）
    snapshots = profile.get('snapshots', [])
    dark_events = 0
    if len(snapshots) >= 2:
        for i in range(1, len(snapshots)):
            try:
                t1 = datetime.fromisoformat(snapshots[i-1]['time'].replace('Z', '+00:00'))
                t2 = datetime.fromisoformat(snapshots[i]['time'].replace('Z', '+00:00'))
                gap_hours = (t2 - t1).total_seconds() / 3600
                if gap_hours > GOING_DARK_GAP_HOURS:
                    dark_events += 1
            except (ValueError, KeyError):
                continue

    if dark_events > 0:
        anomalies.append({
            'type': 'going_dark',
            'description': f'AIS 訊號消失 {dark_events} 次',
            'count': dark_events,
            'severity': 'high' if dark_events >= 3 else 'medium'
        })

    # 船型變更偵測
    types_seen = profile.get('types_seen', [])
    real_types = [t for t in types_seen if t not in ('unknown', 'other')]
    if len(real_types) >= 2:
        anomalies.append({
            'type': 'type_change',
            'description': f'船型變更: {" → ".join(real_types)}',
            'types': real_types,
            'severity': 'medium'
        })

    # 身分變更事件偵測（來自 identity_events.json，近 7 天）
    if identity_events:
        event_count = len(identity_events)
        has_multi = any(ev.get('multi_field') for ev in identity_events)

        if event_count > 0:
            severity = 'high' if event_count >= 3 or has_multi else 'medium'
            # 收集所有欄位變更摘要
            field_changes = []
            for ev in identity_events:
                for ch in ev.get('changes', []):
                    field_changes.append(f"{ch['field']}: {ch['old']} → {ch['new']}")
            anomalies.append({
                'type': 'identity_change',
                'description': f'7 天內 {event_count} 次身分變更',
                'count': event_count,
                'multi_field': has_multi,
                'details': field_changes[:10],
                'severity': severity,
            })

    return anomalies


def classify_vessel(profile, identity_events=None):
    """
    綜合分類單一船隻的可疑程度
    回傳: classification dict
    """
    classification = {
        'mmsi': profile['mmsi'],
        'names': profile.get('names_seen', []),
        'total_snapshots': profile['total_snapshots'],
        'behavioral_threshold': False,
        'ais_anomalies': [],
        'risk_level': 'normal',
        'flags': [],
    }

    # 只對掛漁船旗的船隻做行為分析（CSIS 方法論核心）
    is_fishing = 'fishing' in profile.get('types_seen', [])

    if is_fishing:
        # Criterion 1: 行為比例門檻
        triggered, details = analyze_behavioral_threshold(profile)
        classification['behavioral_threshold'] = triggered
        classification['behavioral_details'] = details
        if triggered:
            classification['flags'].append('行為比例異常：掛漁船旗但不在漁場')

    # Criterion 2: AIS 異常（對所有船型適用，含身分變更事件）
    anomalies = analyze_ais_anomalies(profile, identity_events)
    classification['ais_anomalies'] = anomalies
    if anomalies:
        classification['flags'].extend([a['description'] for a in anomalies])

    # 計算風險等級
    score = 0
    if classification['behavioral_threshold']:
        score += 3
    for a in anomalies:
        if a['severity'] == 'high':
            score += 2
        else:
            score += 1

    if score >= 5:
        classification['risk_level'] = 'critical'
    elif score >= 3:
        classification['risk_level'] = 'high'
    elif score >= 1:
        classification['risk_level'] = 'medium'

    classification['risk_score'] = score
    classification['suspicious'] = score >= 3

    # 附加位置資訊（來自最後快照）
    snapshots = profile.get('snapshots', [])
    if snapshots:
        last = snapshots[-1]
        classification['last_lat'] = last.get('lat')
        classification['last_lon'] = last.get('lon')
        classification['last_seen'] = last.get('time')

    return classification


def main():
    print("=" * 60)
    print("🔍 CSIS 方法論 - 可疑船隻行為分析")
    print("=" * 60)
    print(f"執行時間: {datetime.now(timezone.utc).isoformat()}")

    # 載入身分變更事件（按 MMSI 分組，近 7 天）
    id_events_by_mmsi = load_identity_events()
    id_event_count = sum(len(v) for v in id_events_by_mmsi.values())
    print(f"🔄 已載入身分變更事件: {id_event_count} 筆 ({len(id_events_by_mmsi)} 艘船)")

    history = load_vessel_history()
    if not history:
        # 產生空結果
        output = {
            'updated_at': datetime.now(timezone.utc).isoformat(),
            'methodology': 'CSIS Signals in the Swarm',
            'thresholds': {
                'behavioral': {
                    'fishing_ratio': BEHAVIORAL_FISHING_RATIO,
                },
            },
            'summary': {'total_analyzed': 0, 'suspicious_count': 0},
            'suspicious_vessels': [],
            'all_classifications': [],
        }
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        return

    print(f"\n📊 分析 {len(history)} 艘船隻的歷史行為...")

    classifications = []
    suspicious_vessels = []

    for mmsi, profile in history.items():
        result = classify_vessel(profile, id_events_by_mmsi.get(mmsi))
        classifications.append(result)
        if result['suspicious']:
            suspicious_vessels.append(result)

    # 按風險分數排序
    suspicious_vessels.sort(key=lambda x: x['risk_score'], reverse=True)

    # 統計
    risk_counts = {'critical': 0, 'high': 0, 'medium': 0, 'normal': 0}
    for c in classifications:
        risk_counts[c['risk_level']] += 1

    behavioral_count = sum(1 for c in classifications if c['behavioral_threshold'])
    anomaly_count = sum(1 for c in classifications if c['ais_anomalies'])

    output = {
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'methodology': 'CSIS Signals in the Swarm',
        'thresholds': {
            'behavioral': {
                'fishing_ratio': BEHAVIORAL_FISHING_RATIO,
            },
        },
        'summary': {
            'total_analyzed': len(classifications),
            'suspicious_count': len(suspicious_vessels),
            'behavioral_triggered': behavioral_count,
            'ais_anomaly_detected': anomaly_count,
            'risk_distribution': risk_counts,
        },
        'suspicious_vessels': suspicious_vessels[:50],
        'all_classifications': [c for c in classifications if c['risk_score'] > 0][:100],
    }

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n📋 分析結果:")
    print(f"   分析船隻數: {len(classifications)}")
    print(f"   可疑船隻: {len(suspicious_vessels)}")
    print(f"   行為門檻觸發: {behavioral_count}")
    print(f"   AIS 異常: {anomaly_count}")
    print(f"   風險分布: {risk_counts}")
    print(f"\n📁 結果已輸出至: {OUTPUT_FILE}")


if __name__ == '__main__':
    main()
