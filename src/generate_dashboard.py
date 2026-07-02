#!/usr/bin/env python3
"""
================================================================================
Dashboard 資料生成腳本
Generate dashboard-ready data from vessel monitoring
================================================================================
"""

import json
import re
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path

from io_utils import atomic_write_json

DATA_DIR = Path("data")
DOCS_DIR = Path("docs")
DOCS_DIR.mkdir(exist_ok=True)

# 各資料源的新鮮度設定（供前端「資料健康」狀態表使用）
# key, 中文標籤, 英文標籤, 來源檔, 預期更新間隔（小時）
DATA_SOURCE_SPECS = [
    ('ais',            'AIS 船舶定位',  'AIS vessel positions',      DATA_DIR / 'ais_snapshot.json',       2),
    ('dark_vessels',   'SAR 暗船偵測',  'SAR dark-vessel detection', DATA_DIR / 'dark_vessels.json',       12),
    ('suspicious',     '可疑船隻分析',  'Suspicious-vessel analysis', DATA_DIR / 'suspicious_vessels.json', 12),
    ('ship_transfers', '旁靠偵測',      'Ship-to-ship transfers',    DATA_DIR / 'ship_transfers.json',      2),
    ('identity',       'AIS 身分變更',  'AIS identity changes',      DATA_DIR / 'identity_events.json',      2),
    ('scfi',           'SCFI 運價指數', 'SCFI freight index',        DATA_DIR / 'scfi_history.json',       168),
    ('cable_status',   '海纜狀態',      'Submarine cable status',    DOCS_DIR / 'cable_status.json',        24),
]


def _normalize_iso(v):
    """修正部分來源檔的時間戳瑕疵（例如同時帶 +00:00 與 Z）。"""
    s = v.strip()
    if s.endswith('Z') and ('+00:00' in s or '+0000' in s):
        s = s[:-1]  # 去掉多餘的尾端 Z
    return s


def _source_updated_at(path):
    """取得資料源的 updated_at（ISO 字串），優先讀檔內欄位，否則退回檔案 mtime。"""
    if not path.exists():
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            d = json.load(f)
        if isinstance(d, dict):
            for k in ('updated_at', 'generated_at', 'timestamp', 'last_updated'):
                v = d.get(k)
                if isinstance(v, str) and v:
                    return _normalize_iso(v)
    except (OSError, ValueError):
        pass
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        return mtime.isoformat().replace('+00:00', 'Z')
    except OSError:
        return None


def build_data_sources():
    """組出各資料源的新鮮度清單；status 由前端依 updated_at 即時計算。"""
    sources = []
    for key, label, label_en, path, interval in DATA_SOURCE_SPECS:
        sources.append({
            'key': key,
            'label': label,
            'label_en': label_en,
            'updated_at': _source_updated_at(path),
            'interval_hours': interval,
        })
    return sources


# 暗船每日歷史持久檔（跨執行累積，避免 vessel_monitoring 趨勢凍結）
DARK_HISTORY_PATH = DATA_DIR / 'dark_vessel_history.json'
DARK_HISTORY_MAX_DAYS = 365


def refresh_vessel_monitoring_daily(vessel_data, dark_vessels_data):
    """
    用最新的 dark_vessels.json (overall.dark_by_date) 更新並累積一份持久的
    暗船每日歷史，再覆寫 vessel_monitoring.daily / summary，讓前端趨勢圖
    不再凍結在 vessel_data.json 最後一次手動產生的日期。

    - dark_by_date 只提供每日暗船數；SAR 每日總偵測數無逐日拆分，
      沿用歷史慣例讓 total_detections 與 dark_vessels 相同（不捏造數字）。
    - 首次執行會以既有 vessel_data.json 的 daily 作為種子，保留舊日期的資料。
    """
    # 1. 載入既有持久歷史 {date: {dark_vessels, total_detections}}
    history = {}
    if DARK_HISTORY_PATH.exists():
        try:
            with open(DARK_HISTORY_PATH, 'r', encoding='utf-8') as f:
                history = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"⚠️ 讀取 {DARK_HISTORY_PATH.name} 失敗，將重建: {e}")
            history = {}

    # 2. 種子：用 vessel_data.json 既有 daily 補上歷史尚未涵蓋的日期
    for entry in (vessel_data.get('daily') or []):
        d = entry.get('date')
        if d and d not in history:
            dark = entry.get('dark_vessels', 0)
            history[d] = {
                'dark_vessels': dark,
                'total_detections': entry.get('total_detections', dark),
            }

    # 3. 覆蓋：用最新 dark_by_date 作為這些日期的權威值
    if dark_vessels_data:
        dark_by_date = (dark_vessels_data.get('overall') or {}).get('dark_by_date') or {}
        for d, cnt in dark_by_date.items():
            history[d] = {
                'dark_vessels': cnt,
                'total_detections': cnt,  # SAR 無逐日總數拆分，沿用歷史慣例
            }

    if not history:
        return vessel_data  # 無任何暗船資料可用，維持原樣

    # 4. 修剪保留天數並排序
    for d in sorted(history.keys())[:-DARK_HISTORY_MAX_DAYS] if len(history) > DARK_HISTORY_MAX_DAYS else []:
        del history[d]

    # 5. 持久化
    try:
        atomic_write_json(DARK_HISTORY_PATH, history)
    except IOError as e:
        print(f"⚠️ 寫入 {DARK_HISTORY_PATH.name} 失敗: {e}")

    # 6. 組裝 daily 並覆寫 vessel_monitoring
    daily = [
        {'date': d, 'dark_vessels': history[d]['dark_vessels'],
         'total_detections': history[d]['total_detections']}
        for d in sorted(history.keys())
    ]
    vessel_data['daily'] = daily

    dark_counts = [e['dark_vessels'] for e in daily]
    recent_7d = dark_counts[-7:]
    summary = vessel_data.get('summary') or {}
    summary.update({
        'total_days': len(daily),
        'avg_daily_dark_vessels': round(sum(dark_counts) / len(dark_counts), 1) if dark_counts else 0,
        'avg_daily_detections': round(sum(dark_counts) / len(dark_counts), 1) if dark_counts else 0,
        'recent_7d_avg': round(sum(recent_7d) / len(recent_7d), 1) if recent_7d else 0,
    })
    vessel_data['summary'] = summary

    if daily:
        vessel_data['data_range'] = {'start': daily[0]['date'], 'end': daily[-1]['date']}
    vessel_data['updated_at'] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

    print(f"📈 已更新 vessel_monitoring.daily: {len(daily)} 天 "
          f"({daily[0]['date']} ~ {daily[-1]['date']})")
    return vessel_data

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

    # 用最新暗船資料刷新並累積 vessel_monitoring 每日趨勢（避免凍結在舊日期）
    vessel_data = refresh_vessel_monitoring_daily(vessel_data, dark_vessels_data)

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
                'vessels': ais_raw.get('vessels', [])
            }
            if not ais_snapshot['vessels']:
                print("⚠️ AIS 快照為空 (0 艘船)，跳過以保留前次有效資料")
                ais_snapshot = None
            else:
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

    # 讀取 SCFI vs 船舶流量相關性分析
    scfi_corr_path = DATA_DIR / 'scfi_vessel_correlation.json'
    scfi_correlation_data = None
    if scfi_corr_path.exists():
        try:
            with open(scfi_corr_path, 'r', encoding='utf-8') as f:
                scfi_correlation_data = json.load(f)
            status = scfi_correlation_data.get('status', 'unknown')
            n = scfi_correlation_data.get('sample_size', 0)
            print(f"📊 已載入 SCFI 相關性分析: status={status}, n={n} 週")
        except (json.JSONDecodeError, IOError) as e:
            print(f"⚠️ 讀取 scfi_vessel_correlation.json 失敗: {e}")
    else:
        print("⚠️ 找不到 scfi_vessel_correlation.json，跳過")

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
        'scfi_correlation': scfi_correlation_data,
        'ais_snapshot': ais_snapshot or {'updated_at': '', 'ais_data': {}, 'vessels': []},
        'identity_events': identity_events_data,
        'data_sources': build_data_sources(),
        'status': 'operational',
        'version': '3.3.0'
    }

    # 儲存至 docs 目錄（供 GitHub Pages 使用）
    atomic_write_json(output_path, dashboard)

    print(f"✅ Dashboard 資料已儲存: {output_path}")

    # 資料版本 manifest：前端先抓這個小檔取得版本，再以 data.json?v=<version>
    # 取主資料 —— 同一版資料可被瀏覽器/CDN 快取，新資料才會 bust cache。
    data_version = re.sub(r'[^0-9TZ]', '', dashboard['updated_at'])
    manifest = {
        'data_version': data_version,
        'updated_at': dashboard['updated_at'],
        'files': {'data': f'data.json?v={data_version}'},
    }
    atomic_write_json(DOCS_DIR / 'data-manifest.json', manifest)
    print(f"🔖 資料版本 manifest 已儲存: data-manifest.json (v={data_version})")

    # 複製暗船動畫資料至 docs（獨立檔案，避免主 data.json 過大）
    weekly_dark_path = DATA_DIR / 'weekly_dark_vessels.json'
    if weekly_dark_path.exists():
        shutil.copy2(weekly_dark_path, DOCS_DIR / 'weekly_dark_vessels.json')
        print(f"🎬 已複製暗船動畫資料至 docs/weekly_dark_vessels.json")

    # 複製身分變更事件至 docs（供身分追蹤頁面使用）
    if identity_path.exists():
        shutil.copy2(identity_path, DOCS_DIR / 'identity_events.json')
        print(f"🔄 已複製身分變更事件至 docs/identity_events.json")

    # 複製 AIS 歷史快照至 docs（供前端趨勢圖使用）
    ais_history_path = DATA_DIR / 'ais_history.json'
    if ais_history_path.exists():
        shutil.copy2(ais_history_path, DOCS_DIR / 'ais_history.json')
        print(f"📅 已複製 AIS 歷史快照至 docs/ais_history.json")

    # AIS 軌跡歷史 (tier-1/tier-2) 由 fetch_ais_data.py 直接寫入 docs/，此處不再複製

    # 複製 UN 制裁清單至 docs（供前端制裁警告使用）
    sanctions_path = DATA_DIR / 'un_sanctions_vessels.json'
    if sanctions_path.exists():
        shutil.copy2(sanctions_path, DOCS_DIR / 'un_sanctions_vessels.json')
        print(f"🚫 已複製 UN 制裁清單至 docs/un_sanctions_vessels.json")

    # 複製旁靠偵測資料至 docs（供旁靠偵測頁面使用）
    transfers_path = DATA_DIR / 'ship_transfers.json'
    if transfers_path.exists():
        shutil.copy2(transfers_path, DOCS_DIR / 'ship_transfers.json')
        print(f"🚢 已複製旁靠偵測資料至 docs/ship_transfers.json")

    # 複製 SCFI 歷史資料至 docs（供前端雙軸圖表使用）
    scfi_history_path = DATA_DIR / 'scfi_history.json'
    if scfi_history_path.exists():
        shutil.copy2(scfi_history_path, DOCS_DIR / 'scfi_history.json')
        print(f"📊 已複製 SCFI 歷史資料至 docs/scfi_history.json")


def update_structured_data_dates():
    """Refresh dateModified (data pages) + sitemap <lastmod> (data pages only).

    Evergreen content — blog-*, intro, research, glossary, and /en/ mirrors —
    keeps its existing lastmod so search engines aren't told it changed daily.
    """
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    html_files = [
        'dark-vessels.html', 'statistics.html',
        'identity-history.html', 'ship-transfers.html',
    ]
    date_re = re.compile(r'"dateModified"\s*:\s*"[0-9]{4}-[0-9]{2}-[0-9]{2}"')
    for fname in html_files:
        fpath = DOCS_DIR / fname
        if not fpath.exists():
            continue
        text = fpath.read_text(encoding='utf-8')
        new_text = date_re.sub(f'"dateModified": "{today}"', text)
        if new_text != text:
            fpath.write_text(new_text, encoding='utf-8')

    sitemap = DOCS_DIR / 'sitemap.xml'
    if sitemap.exists():
        text = sitemap.read_text(encoding='utf-8')
        new_text = bump_sitemap_lastmod(text, today)
        new_text = sync_report_sitemap_entries(new_text)
        if new_text != text:
            sitemap.write_text(new_text, encoding='utf-8')
            print(f"📅 已更新 sitemap.xml lastmod（資料型頁面）→ {today}")


# 只有「資料型/每日更新」頁面的 <loc> 才隨資料更新 lastmod。
# 文章型/常青頁面（blog-*, intro, research, glossary, /en/*）保持原值。
SITEMAP_DATA_PAGE_SUFFIXES = (
    'taiwan-grayzone-monitor/',   # 首頁儀表板（根 URL）
    'dark-vessels.html',
    'statistics.html',
    'identity-history.html',
    'ship-transfers.html',
    'ais-animation.html',
    'cn-fishing-animation.html',
    'reports/',                   # 每日報告索引
)


SITE_BASE_URL = 'https://s0914712.github.io/taiwan-grayzone-monitor/'


def sync_report_sitemap_entries(text, reports_dir=None):
    """把 docs/reports/YYYY-MM-DD.html 逐日報告補進 sitemap（GEO：讓搜尋/AI 引擎
    收錄有日期的每日情報頁）。已列出的不重複加；報告發布後內容固定，
    lastmod 用報告日期本身、changefreq=never。"""
    reports_dir = Path(reports_dir) if reports_dir else DOCS_DIR / 'reports'
    if not reports_dir.is_dir() or '</urlset>' not in text:
        return text
    date_name_re = re.compile(r'^\d{4}-\d{2}-\d{2}\.html$')
    entries = []
    for name in sorted(p.name for p in reports_dir.glob('*.html')
                       if date_name_re.match(p.name)):
        loc = f'{SITE_BASE_URL}reports/{name}'
        if f'<loc>{loc}</loc>' in text:
            continue
        entries.append(
            '  <url>\n'
            f'    <loc>{loc}</loc>\n'
            f'    <lastmod>{name[:-5]}</lastmod>\n'
            '    <changefreq>never</changefreq>\n'
            '    <priority>0.4</priority>\n'
            '  </url>\n'
        )
    if entries:
        text = text.replace('</urlset>', ''.join(entries) + '</urlset>')
        print(f"🗺️ sitemap.xml 新增 {len(entries)} 篇每日報告")
    return text


def bump_sitemap_lastmod(text, today):
    """把 sitemap 中『資料型頁面』的 <lastmod> 更新為 today；其餘保持不變。"""
    lastmod_re = re.compile(r'(<lastmod>)[0-9]{4}-[0-9]{2}-[0-9]{2}(</lastmod>)')

    def _bump(block_match):
        block = block_match.group(0)
        loc = re.search(r'<loc>\s*([^<]+?)\s*</loc>', block)
        if loc and loc.group(1).endswith(SITEMAP_DATA_PAGE_SUFFIXES):
            return lastmod_re.sub(rf'\g<1>{today}\2', block)
        return block

    return re.sub(r'<url>.*?</url>', _bump, text, flags=re.S)


if __name__ == "__main__":
    main()
    update_structured_data_dates()
