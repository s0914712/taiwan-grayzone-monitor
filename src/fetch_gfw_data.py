#!/usr/bin/env python3
"""
================================================================================
GFW 資料擷取腳本 - 台灣周邊可疑船隻監測
Taiwan Gray Zone Vessel Monitor - GFW Data Fetcher
================================================================================

功能：
1. 從 GFW API 擷取台灣周邊 SAR 衛星偵測資料
2. 計算可疑船隻指標（暗船、軍演區活動）
3. 儲存至 JSON 供前端使用

資料來源：
- Global Fishing Watch API (SAR Presence)
- 中國海事局航行警告 (待整合)
================================================================================
"""

import os
import json
import requests
from datetime import datetime, timedelta
from pathlib import Path

# =============================================================================
# 設定
# =============================================================================

API_TOKEN = os.environ.get('GFW_API_TOKEN', '')
BASE_URL = "https://gateway.api.globalfishingwatch.org/v3"

HEADERS = {
    "Authorization": f"Bearer {API_TOKEN}",
    "Content-Type": "application/json"
}

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

# 台灣周邊監測區域
TAIWAN_AREA = {
    "type": "Polygon",
    "coordinates": [[
        [117.0, 21.0], [126.0, 21.0], [126.0, 27.0], [117.0, 27.0], [117.0, 21.0]
    ]]
}

# 軍演區域定義（Joint Sword 等）
DRILL_ZONES = {
    "north": {"name": "北區", "coords": [[121.0, 25.5], [122.5, 25.5], [122.5, 26.8], [121.0, 26.8], [121.0, 25.5]]},
    "east": {"name": "東區", "coords": [[122.5, 23.0], [125.0, 23.0], [125.0, 25.5], [122.5, 25.5], [122.5, 23.0]]},
    "south": {"name": "南區", "coords": [[119.0, 21.5], [121.0, 21.5], [121.0, 23.0], [119.0, 23.0], [119.0, 21.5]]},
    "west": {"name": "西區", "coords": [[118.5, 23.5], [120.0, 23.5], [120.0, 25.0], [118.5, 25.0], [118.5, 23.5]]},
}

# =============================================================================
# API 函數
# =============================================================================

def fetch_sar_data(region: dict, start_date: str, end_date: str) -> dict:
    """擷取 SAR 衛星偵測資料"""
    
    params = {
        "datasets[0]": "public-global-sar-presence:latest",
        "date-range": f"{start_date},{end_date}",
        "temporal-resolution": "DAILY",
        "spatial-resolution": "HIGH",
        "spatial-aggregation": "false",
        "format": "JSON"
    }
    
    try:
        response = requests.post(
            f"{BASE_URL}/4wings/report",
            params=params,
            json={"geojson": region},
            headers=HEADERS,
            timeout=120
        )
        
        if response.status_code == 200:
            return response.json()
        else:
            print(f"❌ API 錯誤 {response.status_code}: {response.text[:200]}")
            return {}
            
    except Exception as e:
        print(f"❌ 請求失敗: {e}")
        return {}


def parse_sar_response(data: dict) -> list:
    """解析 SAR API 回應"""
    
    entries = data.get('entries', [])
    if not entries:
        return []
    
    results = []
    for entry in entries:
        for key, values in entry.items():
            if isinstance(values, list):
                for item in values:
                    results.append(item)
    
    return results


# =============================================================================
# 主程式
# =============================================================================

def main():
    print("="*60)
    print("🛰️ GFW 資料擷取 - 台灣周邊可疑船隻監測")
    print("="*60)
    print(f"執行時間: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    
    if not API_TOKEN:
        print("⚠️ 未設定 GFW_API_TOKEN，跳過 GFW 資料收集")
        return
    
    # 計算日期範圍（最近 30 天）
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=30)
    
    start_str = start_date.strftime('%Y-%m-%d')
    end_str = end_date.strftime('%Y-%m-%d')
    
    print(f"\n📅 查詢範圍: {start_str} ~ {end_str}")
    
    # 擷取 SAR 資料
    print("\n📡 擷取 SAR 衛星偵測資料...")
    sar_data = fetch_sar_data(TAIWAN_AREA, start_str, end_str)
    sar_records = parse_sar_response(sar_data)
    
    print(f"   取得 {len(sar_records)} 筆偵測記錄")
    
    # 按日期彙總
    daily_stats = {}
    for record in sar_records:
        date = record.get('date', '')[:10]
        if not date:
            continue
        
        if date not in daily_stats:
            daily_stats[date] = {
                'date': date,
                'total_detections': 0,
                'dark_vessels': 0,
            }
        
        daily_stats[date]['total_detections'] += 1
        
        # 判斷是否為暗船（無 vessel ID）
        if not record.get('vesselId'):
            daily_stats[date]['dark_vessels'] += 1
    
    # 轉換為列表並排序
    daily_list = sorted(daily_stats.values(), key=lambda x: x['date'])
    
    # 計算趨勢
    if len(daily_list) >= 7:
        recent_7d = sum(d['dark_vessels'] for d in daily_list[-7:]) / 7
        previous_7d = sum(d['dark_vessels'] for d in daily_list[-14:-7]) / 7 if len(daily_list) >= 14 else recent_7d
        trend = ((recent_7d - previous_7d) / previous_7d * 100) if previous_7d > 0 else 0
    else:
        recent_7d = 0
        trend = 0
    
    # 建立輸出資料
    output = {
        'updated_at': datetime.utcnow().isoformat() + 'Z',
        'data_range': {
            'start': start_str,
            'end': end_str
        },
        'summary': {
            'total_days': len(daily_list),
            'avg_daily_detections': sum(d['total_detections'] for d in daily_list) / len(daily_list) if daily_list else 0,
            'avg_daily_dark_vessels': sum(d['dark_vessels'] for d in daily_list) / len(daily_list) if daily_list else 0,
            'recent_7d_avg': recent_7d,
            'trend_pct': trend
        },
        'daily': daily_list,
        'drill_zones': DRILL_ZONES,
        'alerts': []
    }
    
    # 檢查是否有異常
    if len(daily_list) >= 2:
        latest = daily_list[-1]
        avg = output['summary']['avg_daily_dark_vessels']
        if latest['dark_vessels'] > avg * 1.5:
            output['alerts'].append({
                'type': 'high_dark_vessels',
                'date': latest['date'],
                'value': latest['dark_vessels'],
                'threshold': avg * 1.5,
                'message': f"暗船數量異常增加: {latest['dark_vessels']} (平均 {avg:.0f})"
            })
    
    # 儲存資料
    output_path = DATA_DIR / 'vessel_data.json'
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ 資料已儲存: {output_path}")
    print(f"   平均每日偵測: {output['summary']['avg_daily_detections']:.0f}")
    print(f"   平均每日暗船: {output['summary']['avg_daily_dark_vessels']:.0f}")
    print(f"   7日趨勢: {trend:+.1f}%")


if __name__ == "__main__":
    main()
