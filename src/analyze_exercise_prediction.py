#!/usr/bin/env python3
"""
================================================================================
軍演預測分析：暗船活動作為軍演預警指標
Exercise Prediction Analysis: Dark Vessel Activity as Early Warning Indicator
================================================================================

功能：
1. 讀取每日暗船偵測資料 (dark_vessels.json)
2. 對照已知軍演事件日期
3. 計算軍演前/中/後的暗船數量變化
4. 計算時間滯後相關性（若有足夠歷史資料）
5. 產出 exercise_prediction.json 供 Dashboard 使用

研究假說：
  軍演前 7-14 天，台灣周邊暗船數量可能出現異常增加，
  可作為軍事活動的早期預警指標。
================================================================================
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

# =============================================================================
# 設定
# =============================================================================

DATA_DIR = Path("data")

# 已知軍演事件清單
# 持續更新：新增事件後，每日分析會自動比對
MILITARY_EXERCISES = [
    {
        "name": "Joint Sword 2024A",
        "name_zh": "聯合利劍-2024A",
        "start": "2024-05-23",
        "end": "2024-05-24",
        "trigger": "賴清德就職",
        "trigger_en": "Lai Ching-te inauguration",
    },
    {
        "name": "Joint Sword 2024B",
        "name_zh": "聯合利劍-2024B",
        "start": "2024-10-14",
        "end": "2024-10-15",
        "trigger": "雙十節演說",
        "trigger_en": "National Day speech",
    },
    {
        "name": "December 2024 Drill",
        "name_zh": "2024年12月軍演",
        "start": "2024-12-09",
        "end": "2024-12-11",
        "trigger": "年度例行",
        "trigger_en": "Routine annual",
    },
    {
        "name": "December 2025 Drill",
        "name_zh": "2025年12月軍演",
        "start": "2025-12-29",
        "end": "2025-12-31",
        "trigger": "年度例行",
        "trigger_en": "Routine annual",
    },
]

# 分析參數
PRE_EXERCISE_DAYS = 14   # 軍演前觀察天數
POST_EXERCISE_DAYS = 7   # 軍演後觀察天數
ANOMALY_THRESHOLD = 1.5  # 異常門檻（倍數於平均值）


# =============================================================================
# 分析函數
# =============================================================================

def load_dark_vessel_data():
    """載入暗船偵測每日資料"""
    dark_path = DATA_DIR / 'dark_vessels.json'
    if not dark_path.exists():
        print("⚠️ 找不到 dark_vessels.json")
        return {}, {}

    with open(dark_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # 整體每日暗船數
    overall_daily = data.get('overall', {}).get('dark_by_date', {})

    # 各區域每日暗船數
    regions_daily = {}
    for region_id, region_data in data.get('regions', {}).items():
        regions_daily[region_id] = {
            'name': region_data.get('name', region_id),
            'daily': region_data.get('dark_by_date', {}),
        }

    return overall_daily, regions_daily


def compute_period_stats(daily_data, start_date, end_date):
    """計算指定期間的暗船統計"""
    values = []
    current = start_date
    while current <= end_date:
        date_str = current.strftime('%Y-%m-%d')
        if date_str in daily_data:
            values.append(daily_data[date_str])
        current += timedelta(days=1)

    if not values:
        return None

    return {
        'count': len(values),
        'total': sum(values),
        'mean': round(sum(values) / len(values), 1),
        'max': max(values),
        'min': min(values),
    }


def analyze_exercise_event(exercise, overall_daily, regions_daily):
    """分析單一軍演事件前後的暗船變化"""
    ex_start = datetime.strptime(exercise['start'], '%Y-%m-%d')
    ex_end = datetime.strptime(exercise['end'], '%Y-%m-%d')

    # 三個時期的日期範圍
    pre_start = ex_start - timedelta(days=PRE_EXERCISE_DAYS)
    pre_end = ex_start - timedelta(days=1)
    post_start = ex_end + timedelta(days=1)
    post_end = ex_end + timedelta(days=POST_EXERCISE_DAYS)

    # 整體暗船統計
    pre_stats = compute_period_stats(overall_daily, pre_start, pre_end)
    during_stats = compute_period_stats(overall_daily, ex_start, ex_end)
    post_stats = compute_period_stats(overall_daily, post_start, post_end)

    # 計算變化百分比
    change_pct = None
    if pre_stats and during_stats and pre_stats['mean'] > 0:
        change_pct = round(
            (during_stats['mean'] - pre_stats['mean']) / pre_stats['mean'] * 100, 1
        )

    # 各區域統計
    region_analysis = {}
    for region_id, region_info in regions_daily.items():
        r_daily = region_info['daily']
        r_pre = compute_period_stats(r_daily, pre_start, pre_end)
        r_during = compute_period_stats(r_daily, ex_start, ex_end)
        r_post = compute_period_stats(r_daily, post_start, post_end)

        r_change = None
        if r_pre and r_during and r_pre['mean'] > 0:
            r_change = round(
                (r_during['mean'] - r_pre['mean']) / r_pre['mean'] * 100, 1
            )

        region_analysis[region_id] = {
            'name': region_info['name'],
            'before': r_pre,
            'during': r_during,
            'after': r_post,
            'change_pct': r_change,
        }

    result = {
        'name': exercise['name'],
        'name_zh': exercise['name_zh'],
        'start': exercise['start'],
        'end': exercise['end'],
        'trigger': exercise['trigger'],
        'trigger_en': exercise['trigger_en'],
        'has_data': pre_stats is not None or during_stats is not None,
        'overall': {
            'before': pre_stats,
            'during': during_stats,
            'after': post_stats,
            'change_pct': change_pct,
        },
        'regions': region_analysis,
    }

    return result


def compute_daily_baseline(overall_daily):
    """計算每日暗船基準線統計"""
    values = list(overall_daily.values())
    if not values:
        return {'mean': 0, 'std': 0, 'anomaly_threshold': 0}

    mean_val = sum(values) / len(values)
    variance = sum((v - mean_val) ** 2 for v in values) / len(values)
    std_val = variance ** 0.5

    return {
        'mean': round(mean_val, 1),
        'std': round(std_val, 1),
        'anomaly_threshold': round(mean_val * ANOMALY_THRESHOLD, 1),
        'total_days': len(values),
    }


def detect_anomaly_days(overall_daily, baseline):
    """偵測暗船數異常偏高的日期"""
    threshold = baseline['anomaly_threshold']
    anomalies = []

    for date_str, count in sorted(overall_daily.items()):
        if count > threshold:
            anomalies.append({
                'date': date_str,
                'dark_vessels': count,
                'ratio_to_mean': round(count / baseline['mean'], 2) if baseline['mean'] > 0 else 0,
            })

    return anomalies


def check_proximity_to_exercises(anomalies, exercises):
    """檢查異常日期是否接近軍演"""
    for anomaly in anomalies:
        anomaly_date = datetime.strptime(anomaly['date'], '%Y-%m-%d')
        anomaly['near_exercise'] = None

        for ex in exercises:
            ex_start = datetime.strptime(ex['start'], '%Y-%m-%d')
            ex_end = datetime.strptime(ex['end'], '%Y-%m-%d')
            delta_start = (ex_start - anomaly_date).days
            delta_end = (anomaly_date - ex_end).days

            if 0 <= delta_start <= PRE_EXERCISE_DAYS:
                anomaly['near_exercise'] = {
                    'name': ex['name'],
                    'days_before': delta_start,
                    'relation': 'before',
                }
                break
            elif ex_start <= anomaly_date <= ex_end:
                anomaly['near_exercise'] = {
                    'name': ex['name'],
                    'days_before': 0,
                    'relation': 'during',
                }
                break
            elif 0 <= delta_end <= POST_EXERCISE_DAYS:
                anomaly['near_exercise'] = {
                    'name': ex['name'],
                    'days_after': delta_end,
                    'relation': 'after',
                }
                break

    return anomalies


# =============================================================================
# 主程式
# =============================================================================

def main():
    print("=" * 60)
    print("🎯 軍演預測分析 - 暗船活動預警指標")
    print("=" * 60)
    print(f"執行時間: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")

    # 載入資料
    overall_daily, regions_daily = load_dark_vessel_data()

    if not overall_daily:
        print("⚠️ 無暗船每日資料，無法進行分析")
        # 仍輸出軍演清單供 dashboard 顯示
        output = {
            'updated_at': datetime.utcnow().isoformat() + 'Z',
            'exercises': MILITARY_EXERCISES,
            'analysis': [],
            'baseline': {},
            'anomalies': [],
            'data_available': False,
        }
        output_path = DATA_DIR / 'exercise_prediction.json'
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"✅ 已儲存（僅軍演清單）: {output_path}")
        return

    dates = sorted(overall_daily.keys())
    print(f"\n📊 暗船資料範圍: {dates[0]} ~ {dates[-1]} ({len(dates)} 天)")
    print(f"   已知軍演事件: {len(MILITARY_EXERCISES)} 場")

    # 基準線統計
    baseline = compute_daily_baseline(overall_daily)
    print(f"\n📏 基準線統計:")
    print(f"   平均暗船數/天: {baseline['mean']}")
    print(f"   標準差: {baseline['std']}")
    print(f"   異常門檻 (×{ANOMALY_THRESHOLD}): {baseline['anomaly_threshold']}")

    # 軍演事件分析
    print(f"\n📋 軍演事件研究:")
    print("-" * 70)

    exercise_results = []
    for ex in MILITARY_EXERCISES:
        result = analyze_exercise_event(ex, overall_daily, regions_daily)
        exercise_results.append(result)

        if result['has_data']:
            before = result['overall']['before']
            during = result['overall']['during']
            change = result['overall']['change_pct']

            before_str = f"{before['mean']:.0f}" if before else "N/A"
            during_str = f"{during['mean']:.0f}" if during else "N/A"
            change_str = f"{change:+.1f}%" if change is not None else "N/A"

            print(f"  {ex['name_zh']:<20} {ex['start']}")
            print(f"    前14天平均: {before_str}  軍演期間: {during_str}  變化: {change_str}")
        else:
            print(f"  {ex['name_zh']:<20} {ex['start']}  (資料範圍外)")

    # 異常日偵測
    anomalies = detect_anomaly_days(overall_daily, baseline)
    anomalies = check_proximity_to_exercises(anomalies, MILITARY_EXERCISES)

    print(f"\n⚠️ 異常日偵測 (暗船 > {baseline['anomaly_threshold']:.0f}):")
    if anomalies:
        for a in anomalies:
            near = a.get('near_exercise')
            near_str = ""
            if near:
                if near['relation'] == 'before':
                    near_str = f" ← {near['name']} 前 {near['days_before']} 天"
                elif near['relation'] == 'during':
                    near_str = f" ← {near['name']} 期間"
                elif near['relation'] == 'after':
                    near_str = f" ← {near['name']} 後 {near['days_after']} 天"
            print(f"  {a['date']}: {a['dark_vessels']} (×{a['ratio_to_mean']:.1f}){near_str}")
    else:
        print("  無異常日")

    # 產出結果
    output = {
        'updated_at': datetime.utcnow().isoformat() + 'Z',
        'data_range': {
            'start': dates[0],
            'end': dates[-1],
        },
        'exercises': MILITARY_EXERCISES,
        'analysis': exercise_results,
        'baseline': baseline,
        'anomalies': anomalies,
        'parameters': {
            'pre_exercise_days': PRE_EXERCISE_DAYS,
            'post_exercise_days': POST_EXERCISE_DAYS,
            'anomaly_threshold_multiplier': ANOMALY_THRESHOLD,
        },
        'data_available': True,
    }

    output_path = DATA_DIR / 'exercise_prediction.json'
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 分析結果已儲存: {output_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
