#!/usr/bin/env python3
"""
================================================================================
ITU MARS 船舶登記資料查詢
ITU Maritime Mobile Access & Retrieval System — Ship Station Lookup
================================================================================

查詢 ITU GISIS Ship Station List 取得船舶官方登記資料（船名、呼號、MMSI、
IMO、管理國），用於交叉比對 AIS 回報資訊。

功能：
  - 單一 MMSI 查詢
  - 批次 MMSI 查詢（含速率限制）
  - 本地 JSON 快取（避免重複請求）
  - 可由 analyze_suspicious.py 匯入使用

用法：
  # 命令列模式
  python3 src/lookup_itu_mars.py 374942000 412345678 ...

  # 匯入模式
  from src.lookup_itu_mars import lookup_mmsi, batch_lookup
  result = lookup_mmsi('374942000')
  results = batch_lookup(['374942000', '412345678'])
================================================================================
"""

import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("⚠️ 需安裝 requests 和 beautifulsoup4: pip install requests beautifulsoup4")
    sys.exit(1)

# ── 設定 ──────────────────────────────────────────────────
DATA_DIR = Path("data")
CACHE_FILE = DATA_DIR / "itu_mars_cache.json"
CACHE_EXPIRY_DAYS = 30          # 快取有效期 30 天
REQUEST_DELAY_SEC = 2.0         # 每次查詢間隔（秒），避免被封鎖
MAX_RETRIES = 2                 # 查詢失敗重試次數

ITU_URL = "https://www.itu.int/mmsapp/ShipStation/list"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": ITU_URL,
    "Origin": "https://www.itu.int",
    "Content-Type": "application/x-www-form-urlencoded",
}

# ── 快取管理 ──────────────────────────────────────────────
_cache = None


def _load_cache():
    """載入本地快取"""
    global _cache
    if _cache is not None:
        return _cache

    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                _cache = json.load(f)
        except Exception:
            _cache = {}
    else:
        _cache = {}
    return _cache


def _save_cache():
    """儲存快取至檔案"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(_cache, f, ensure_ascii=False, indent=2)


def _is_cache_valid(entry):
    """檢查快取條目是否在有效期內"""
    try:
        cached_at = datetime.fromisoformat(entry.get('cached_at', ''))
        return datetime.now(timezone.utc) - cached_at < timedelta(days=CACHE_EXPIRY_DAYS)
    except (ValueError, TypeError):
        return False


# ── ITU MARS 查詢 ────────────────────────────────────────

def _create_session():
    """建立帶 WAF cookie 管理的 requests session"""
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def _get_breadcrumb(session):
    """取得動態 CSRF token (Breadcrumb)"""
    resp = session.get(ITU_URL, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, 'html.parser')
    tag = soup.find("input", {"name": "Breadcrumb"})
    return tag.get("value", "") if tag else ""


def _parse_result_table(html):
    """
    解析 ITU MARS 回傳的 HTML 表格。
    回傳 list[dict]，每個 dict 代表一筆船舶記錄。
    """
    soup = BeautifulSoup(html, 'html.parser')
    tables = soup.find_all("table")
    if not tables:
        return []

    rows = tables[0].find_all("tr")
    if len(rows) < 2:
        return []

    # 解析表頭
    header_cells = rows[0].find_all(['th', 'td'])
    headers = [c.text.strip() for c in header_cells]

    # 欄位名稱映射
    field_map = {
        'Ship Name': 'ship_name',
        'Call Sign': 'call_sign',
        'MMSI': 'mmsi',
        'Administration': 'administration',
        'Geographical Area': 'geo_area',
        'Ship (Vessel) ID Number': 'imo_number',
        'Update Date': 'update_date',
    }

    results = []
    for row in rows[1:]:
        cells = row.find_all(['td', 'th'])
        values = [c.text.strip() for c in cells]
        if len(values) != len(headers):
            continue

        record = {}
        for h, v in zip(headers, values):
            key = field_map.get(h, h.lower().replace(' ', '_'))
            record[key] = v

        if record.get('mmsi'):
            results.append(record)

    return results


def lookup_mmsi(mmsi, session=None, breadcrumb=None):
    """
    查詢單一 MMSI 的 ITU MARS 登記資料。

    Args:
        mmsi: MMSI 號碼（字串）
        session: 可選的 requests.Session（批次查詢時共用）
        breadcrumb: 可選的 Breadcrumb token（批次查詢時共用）

    Returns:
        dict: 查詢結果，包含 ship_name, call_sign, mmsi, administration,
              imo_number, update_date 等欄位。
              查無結果時回傳 {'mmsi': mmsi, 'found': False}
    """
    mmsi = str(mmsi).strip()

    # 先查快取
    cache = _load_cache()
    if mmsi in cache and _is_cache_valid(cache[mmsi]):
        return cache[mmsi]

    # 建立連線
    own_session = session is None
    if own_session:
        session = _create_session()

    try:
        if breadcrumb is None:
            breadcrumb = _get_breadcrumb(session)

        payload = {
            "Breadcrumb": breadcrumb,
            "ScrollTopValue": "400",
            "Search.Name": "",
            "Search.MaritimeMobileServiceIdentity": mmsi,
            "Search.CallSign": "",
            "Search.VesselIdentificationNumber": "",
            "Search.EmergencyPositionIndicatingRadioBeaconHexadecimalIdentifier": "",
            "Search.SatelliteNumber": "",
            "Search.Administration.SelectedId": "",
            "Search.GeographicalArea.SelectedId": "",
            "Search.GeneralClassification.SelectedId": "",
            "viewCommand": "Search",
        }

        resp = session.post(ITU_URL, data=payload, timeout=15)
        resp.raise_for_status()

        records = _parse_result_table(resp.text)

        if records:
            result = records[0]  # 取第一筆匹配
            result['found'] = True
        else:
            result = {'mmsi': mmsi, 'found': False}

        # 寫入快取
        result['cached_at'] = datetime.now(timezone.utc).isoformat()
        cache[mmsi] = result
        _save_cache()

        return result

    except requests.exceptions.RequestException as e:
        print(f"  ⚠️ MMSI {mmsi} 查詢失敗: {e}")
        return {'mmsi': mmsi, 'found': False, 'error': str(e)}


def batch_lookup(mmsi_list, progress=True):
    """
    批次查詢多個 MMSI。

    Args:
        mmsi_list: MMSI 字串列表
        progress: 是否顯示進度

    Returns:
        dict: {mmsi: result_dict, ...}
    """
    results = {}
    cache = _load_cache()

    # 分離已快取 vs 需查詢
    to_fetch = []
    for mmsi in mmsi_list:
        mmsi = str(mmsi).strip()
        if mmsi in cache and _is_cache_valid(cache[mmsi]):
            results[mmsi] = cache[mmsi]
        else:
            to_fetch.append(mmsi)

    if progress and results:
        print(f"  📦 快取命中: {len(results)}/{len(mmsi_list)}")

    if not to_fetch:
        return results

    if progress:
        print(f"  🌐 需查詢 ITU MARS: {len(to_fetch)} 筆")

    # 建立共用 session + breadcrumb
    session = _create_session()
    breadcrumb = None

    for i, mmsi in enumerate(to_fetch):
        if progress and (i % 10 == 0 or i == len(to_fetch) - 1):
            print(f"  查詢 {i+1}/{len(to_fetch)}: MMSI {mmsi}")

        # 每 20 次重新取 breadcrumb（避免 token 過期）
        if breadcrumb is None or i % 20 == 0:
            try:
                breadcrumb = _get_breadcrumb(session)
            except Exception as e:
                print(f"  ⚠️ 取得 Breadcrumb 失敗: {e}")
                breadcrumb = ""

        for attempt in range(MAX_RETRIES + 1):
            result = lookup_mmsi(mmsi, session=session, breadcrumb=breadcrumb)
            if result.get('found') or 'error' not in result:
                break
            if attempt < MAX_RETRIES:
                time.sleep(REQUEST_DELAY_SEC * 2)
                # 重新建立 session
                session = _create_session()
                try:
                    breadcrumb = _get_breadcrumb(session)
                except Exception:
                    breadcrumb = ""

        results[mmsi] = result

        # 速率限制
        if i < len(to_fetch) - 1:
            time.sleep(REQUEST_DELAY_SEC)

    return results


def get_cached_data():
    """回傳完整快取（供 analyze_suspicious.py 使用）"""
    return _load_cache()


# ── CLI 入口 ─────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("用法: python3 src/lookup_itu_mars.py <MMSI> [MMSI2] ...")
        print("範例: python3 src/lookup_itu_mars.py 374942000 412345678")
        sys.exit(1)

    mmsi_list = sys.argv[1:]
    print(f"🔍 ITU MARS Ship Station 查詢")
    print(f"   查詢 {len(mmsi_list)} 個 MMSI")
    print("=" * 50)

    results = batch_lookup(mmsi_list)

    for mmsi, data in results.items():
        print(f"\nMMSI: {mmsi}")
        if data.get('found'):
            print(f"  船名:   {data.get('ship_name', '—')}")
            print(f"  呼號:   {data.get('call_sign', '—')}")
            print(f"  IMO:    {data.get('imo_number', '—')}")
            print(f"  管理國: {data.get('administration', '—')}")
            print(f"  區域:   {data.get('geo_area', '—')}")
            print(f"  更新:   {data.get('update_date', '—')}")
        else:
            err = data.get('error', '')
            print(f"  查無資料{' (' + err + ')' if err else ''}")

    print(f"\n📁 快取: {CACHE_FILE}")


if __name__ == '__main__':
    main()
