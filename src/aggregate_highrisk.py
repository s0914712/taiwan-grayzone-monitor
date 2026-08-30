#!/usr/bin/env python3
"""
================================================================================
高風險船舶 週報/月報 彙整引擎
High-Risk Vessel Weekly / Monthly Aggregation
================================================================================

三個模式（`--mode`）：

* ``accumulate`` — 每次 update-data.yml 執行（2 次/日）：讀
  `data/highrisk_snapshot.json`（analyze_suspicious 寫出的完整 suspicious
  清單，gitignored），優先排序取前 `DAILY_MAX_ROWS` 艘（critical → 海纜
  滯留 → 離岸滯留 → 分數），對入選船做航程 enrichment（出港/進港港口、
  滯留海上時間 — 由 tier-1/2 航跡的港內↔海上轉換推得），合併進滾動累積檔
  `data/highrisk_accumulator.json`（committed，保留 45 天）。

* ``weekly`` — 週一至週三（UTC）若上一 ISO 週的輸出檔不存在則產生
  `docs/reports/weekly/<YYYY-Www>.json` + `.csv`：逐船彙整（船種/船籍/
  港口/海上時間/海纜滯留時數與均速）+ 0.1° 徘徊熱區格網 + 統計摘要。

* ``monthly`` — 每月 1-3 日（UTC）產生上一日曆月的
  `docs/reports/monthly/<YYYY-MM>.json` + `.csv`（同 schema，外加逐週
  mini 摘要與既有日報系列的月趨勢）— 直接從 accumulator 的日列彙整，
  不從週檔轉算（ISO 週會跨月界）。

設計備忘：
* 僅 stdlib + 專案模組（tests.yml 只裝 requests+pytest，不可 import pandas）。
* 日期一律 UTC；報表標示的區間是 UTC 日界。
* 事件列 [lat, lon, hours, avg_kn, start_date] 帶起始日 — 14 天航跡視窗下
  同一徘徊事件會連續多天出現在快照，彙整時以 (center, start_date) 去重、
  並只計「起始日落在報表區間內」的事件，否則熱區時數會被重複灌水。
* 出港/進港港口查 `geofence.PORTS`/`CN_PORTS`；CN 側只涵蓋閩/浙南/粵東，
  上海以北出發的船必然查無出港港口 — 以 `observed_span_only` 標明
  「觀測期間未見靠港」，不能與真實航程混為一談。
"""
import argparse
import csv
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from io_utils import atomic_write_json, load_json
from grid_utils import build_stat_grid

DATA_DIR = Path('data')
DOCS_DIR = Path('docs')
SNAPSHOT_FILE = DATA_DIR / 'highrisk_snapshot.json'
ACCUMULATOR_FILE = DATA_DIR / 'highrisk_accumulator.json'
MID_FLAGS_FILE = DATA_DIR / 'mid_flags.json'
DAILY_REPORTS_DIR = DOCS_DIR / 'reports'   # generate_report.py 的日報 JSON
WEEKLY_DIR = DAILY_REPORTS_DIR / 'weekly'
MONTHLY_DIR = DAILY_REPORTS_DIR / 'monthly'

RETENTION_DAYS = 45          # 涵蓋 31 天日曆月 + 產生期限的 grace
DAILY_MAX_ROWS = 400         # 每日進累積檔的船數上限（優先排序後截斷）
REPORT_MAX_VESSELS = 500     # 週/月報逐船明細上限（摘要仍涵蓋全部）
HOTSPOT_MAX_CELLS = 50
EVENT_CAP_PER_DAY = 5        # 與 analyze_suspicious.build_loiter_events 一致
CABLE_LOITER_MIN_HOURS = 3.0  # 與 analyze_suspicious.CABLE_LOITER_HOURS 一致
OFFSHORE_LOITER_MIN_DAYS = 5.0
GOV_TYPES = ('coastguard', 'msa', 'rescue', 'research')
RISK_ORDER = {'critical': 0, 'high': 1, 'medium': 2, 'normal': 3}
WEEKLY_GRACE_WEEKDAYS = (0, 1, 2)   # 週一~三（GitHub cron 延遲/漏跑是常態）
MONTHLY_GRACE_DAYS = 3              # 每月 1-3 日

CSV_COLUMNS = [
    'mmsi', 'name', 'vessel_type', 'gov_category',
    'flag_mid', 'flag_en', 'flag_zh',
    'max_risk_score', 'risk_level', 'days_seen',
    'cable_loiter_hours', 'cable_loiter_avg_speed_kn', 'cable_loiter_events',
    'cables_nearby', 'offshore_loiter_days',
    'non_top10_flag', 'sanctioned',
    'departure_port', 'departure_time_utc',
    'arrival_port', 'arrival_time_utc',
    'time_at_sea_hours', 'time_at_sea_note',
    'last_lat', 'last_lon', 'last_zone', 'last_seen_utc',
]


# ══════════════════════════════════════════════════════════════════
# 船籍（MMSI MID → 國名）
# ══════════════════════════════════════════════════════════════════

def load_mid_flags(path=MID_FLAGS_FILE):
    """載入 MID→國名表（data/mid_flags.json，與 docs/js/map-data.js 的
    MID_FLAG_TABLE 由 tests/test_mid_flags.py 保持同步）。"""
    return load_json(path, {}, expect_type=dict)


def flag_for_mmsi(mmsi, table):
    """MMSI 前 3 碼 (MID) → {'mid', 'en', 'zh'}；查無/過短給 Unknown。"""
    mid = str(mmsi or '')[:3]
    if len(mid) < 3:
        return {'mid': mid, 'en': 'Unknown', 'zh': '未知'}
    entry = table.get(mid)
    if not entry:
        return {'mid': mid, 'en': 'Unknown', 'zh': '未知'}
    return {'mid': mid, 'en': entry.get('en', 'Unknown'),
            'zh': entry.get('zh', '未知')}


# ══════════════════════════════════════════════════════════════════
# 每日選船（優先排序 + 截斷）
# ══════════════════════════════════════════════════════════════════

def select_daily_highrisk(rows, max_rows=DAILY_MAX_ROWS):
    """優先排序取前 max_rows 艘（純函式）。

    排序：critical 優先 → 海纜滯留 → 離岸滯留 → 分數降冪 → MMSI（穩定）。
    高風險船 ~1750 艘且大多是海纜旁低速作業的漁船，全收會讓累積檔失控
    （本 repo 已被大檔撐爆過）；統計摘要另計全量，只有逐船明細被截斷。
    """
    def key(r):
        return (
            0 if r.get('risk_level') == 'critical' else 1,
            0 if r.get('cable_loitering') else 1,
            0 if r.get('offshore_loitering') else 1,
            -(r.get('risk_score') or 0),
            str(r.get('mmsi') or ''),
        )
    return sorted(rows, key=key)[:max_rows]


# ══════════════════════════════════════════════════════════════════
# 航程切分（出港/進港/海上時間）
# ══════════════════════════════════════════════════════════════════

def _parse_ts(ts):
    try:
        return datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return None


def empty_voyage():
    return {'as_of': None, 'departure_port': None, 'departure_time': None,
            'arrival_port': None, 'arrival_time': None,
            'at_sea_hours': None, 'observed_span_only': True}


def segment_voyages(points, port_lookup):
    """從航跡點推出港/進港與海上時間（純函式，port_lookup 可注入）。

    points: [{'t', 'lat', 'lon', ...}]（未必排序）。port_lookup(lat, lon)
    → 港名或 None（生產環境用 geofence.is_in_port_cached）。

    邏輯：逐點標記在港/海上 → 壓成交替的 run → 取最近一段：
    * 最後在海上：出港 = 前一個港 run 的港名/離港時間；海上時間 = 尾段
      海上 run 的跨度；進港 = None。
    * 最後在港內：進港 = 尾段港 run 的港名/抵達時間；海上時間 = 其前一段
      海上 run 跨度；出港 = 再前一個港 run。
    * 全程無港：observed_span_only=True，海上時間 = 整段觀測跨度 —
      「未觀測到靠港」≠「沒有靠港」（CN_PORTS 只涵蓋閩/浙南/粵東）。

    注意：AIS 快照 2h 一筆，短暫進出港可能被跳過；港以點+半徑近似。
    """
    seq = []
    for p in points or []:
        t = _parse_ts(p.get('t'))
        if t is None or p.get('lat') is None or p.get('lon') is None:
            continue
        seq.append((t, p['lat'], p['lon']))
    if not seq:
        return empty_voyage()
    seq.sort(key=lambda x: x[0])

    # 壓成交替 run：{'port': 港名或 None, 'start': t, 'end': t}
    runs = []
    for t, lat, lon in seq:
        port = port_lookup(lat, lon)
        state = bool(port)
        if runs and bool(runs[-1]['port']) == state:
            runs[-1]['end'] = t
            if port:
                runs[-1]['port'] = port
        else:
            runs.append({'port': port if port else None, 'start': t, 'end': t})

    as_of = seq[-1][0].isoformat()

    def hours(run):
        return round((run['end'] - run['start']).total_seconds() / 3600, 1)

    if not any(r['port'] for r in runs):
        return {'as_of': as_of, 'departure_port': None, 'departure_time': None,
                'arrival_port': None, 'arrival_time': None,
                'at_sea_hours': round(
                    (seq[-1][0] - seq[0][0]).total_seconds() / 3600, 1),
                'observed_span_only': True}

    last = runs[-1]
    if last['port']:
        # 目前在港內 → 已完成一段航程（或全程在港）
        sea = runs[-2] if len(runs) >= 2 else None
        dep = runs[-3] if len(runs) >= 3 else None
        return {'as_of': as_of,
                'departure_port': dep['port'] if dep else None,
                'departure_time': dep['end'].isoformat() if dep else None,
                'arrival_port': last['port'],
                'arrival_time': last['start'].isoformat(),
                'at_sea_hours': hours(sea) if sea else 0.0,
                'observed_span_only': False}

    # 目前在海上
    dep = runs[-2] if len(runs) >= 2 else None
    return {'as_of': as_of,
            'departure_port': dep['port'] if dep else None,
            'departure_time': dep['end'].isoformat() if dep else None,
            'arrival_port': None, 'arrival_time': None,
            'at_sea_hours': hours(last),
            'observed_span_only': dep is None}


# ══════════════════════════════════════════════════════════════════
# 累積檔（rolling accumulator）
# ══════════════════════════════════════════════════════════════════

def new_accumulator():
    return {'version': 1, 'updated_at': None,
            'retention_days': RETENTION_DAYS, 'vessels': {}, 'daily': {}}


def _ev_key(e):
    """事件跨日去重鍵：中心座標（2dp ≈ 1km）+ 起始日。同一事件在後續
    執行中可能還在增長（時數變大、中心微移），粗化座標吸收這種漂移。"""
    start = e[4] if len(e) > 4 else None
    return (round(e[0], 2), round(e[1], 2), start)


def _merge_events(old, new, cap=EVENT_CAP_PER_DAY):
    """以 _ev_key 去重合併事件列，同鍵取時數較大者，依時數降冪 cap。"""
    merged = {}
    for e in list(old or []) + list(new or []):
        k = _ev_key(e)
        if k not in merged or (e[2] or 0) > (merged[k][2] or 0):
            merged[k] = list(e)
    out = sorted(merged.values(), key=lambda e: -(e[2] or 0))
    return out[:cap]


def merge_into_accumulator(acc, date_key, rows, metas,
                           retention_days=RETENTION_DAYS, now_iso=None):
    """把一日的高風險列與船隻 meta 合併進累積檔（純函式，就地修改後回傳）。

    per-(date, mmsi) 取 max（分數/滯留時數/離岸天數）、事件去重 union、
    meta latest-wins；daily 只留最近 retention_days 天；沒有任何日列的
    船隻 meta 一併剪除。輸出鍵排序（git delta 才穩定）。
    """
    daily = acc.setdefault('daily', {})
    vessels = acc.setdefault('vessels', {})
    day = daily.setdefault(date_key, {})

    for row in rows:
        mmsi = str(row.get('mmsi') or '')
        if not mmsi:
            continue
        new = {
            's': row.get('risk_score') or 0,
            'lv': row.get('risk_level') or 'normal',
            'lh': row.get('loiter_h') or 0,
            'lk': row.get('loiter_kn'),
            'od': row.get('off_days') or 0,
            'cb': sorted(set(row.get('cables') or [])),
            'ev': [list(e) for e in (row.get('ev') or [])],
            'nt': 1 if row.get('non_top10_flag') else 0,
            'sx': 1 if row.get('sanctioned') else 0,
        }
        rec = day.get(mmsi)
        if rec is None:
            day[mmsi] = new
            continue
        if new['s'] >= rec.get('s', 0):
            rec['s'] = new['s']
            rec['lv'] = new['lv']
        if new['lh'] >= rec.get('lh', 0):
            rec['lh'] = new['lh']
            rec['lk'] = new['lk']
        rec['od'] = max(rec.get('od', 0), new['od'])
        rec['cb'] = sorted(set(rec.get('cb', [])) | set(new['cb']))[:5]
        rec['ev'] = _merge_events(rec.get('ev'), new['ev'])
        rec['nt'] = max(rec.get('nt', 0), new['nt'])
        rec['sx'] = max(rec.get('sx', 0), new['sx'])

    for mmsi, meta in (metas or {}).items():
        vessels[str(mmsi)] = meta

    keep = sorted(daily)[-retention_days:]
    live = set()
    for d in keep:
        live.update(daily[d].keys())
    acc['daily'] = {d: {m: daily[d][m] for m in sorted(daily[d])}
                    for d in keep}
    acc['vessels'] = {m: vessels[m] for m in sorted(vessels) if m in live}
    acc['version'] = 1
    acc['retention_days'] = retention_days
    acc['updated_at'] = now_iso or datetime.now(timezone.utc).isoformat()
    return acc


# ══════════════════════════════════════════════════════════════════
# 區間彙整（週/月共用）
# ══════════════════════════════════════════════════════════════════

def bucket_range(acc, start_key, end_key):
    """彙整 [start_key, end_key]（含）的日列 → 逐船 aggregate（純函式）。

    事件只計「起始日落在區間內」的（14 天航跡視窗會把更早的事件帶進
    當日快照，不濾會重複灌水）；無起始日的舊格式事件保留。
    回傳 (per_mmsi dict, daily_counts, days_present)。
    """
    daily = acc.get('daily', {})
    days = [d for d in sorted(daily) if start_key <= d <= end_key]
    vessels = {}
    daily_counts = {}
    for d in days:
        daily_counts[d] = len(daily[d])
        for mmsi, rec in daily[d].items():
            v = vessels.setdefault(mmsi, {
                'days_seen': 0, 'max_score': 0, 'risk_level': 'normal',
                'loiter_hours': 0.0, 'loiter_kn_fallback': None,
                'cables': set(), 'off_days': 0.0, 'events': {},
                'non_top10': False, 'sanctioned': False})
            v['days_seen'] += 1
            s = rec.get('s', 0)
            if (s > v['max_score']
                    or (s == v['max_score']
                        and RISK_ORDER.get(rec.get('lv'), 3)
                        < RISK_ORDER.get(v['risk_level'], 3))):
                v['max_score'] = s
                v['risk_level'] = rec.get('lv', 'normal')
            if (rec.get('lh') or 0) >= v['loiter_hours']:
                v['loiter_hours'] = rec.get('lh') or 0
                v['loiter_kn_fallback'] = rec.get('lk')
            v['off_days'] = max(v['off_days'], rec.get('od') or 0)
            v['cables'].update(rec.get('cb') or [])
            for e in rec.get('ev') or []:
                start = e[4] if len(e) > 4 else None
                if start and not (start_key <= start <= end_key):
                    continue
                k = _ev_key(e)
                if k not in v['events'] or (e[2] or 0) > (v['events'][k][2] or 0):
                    v['events'][k] = e
            v['non_top10'] = v['non_top10'] or bool(rec.get('nt'))
            v['sanctioned'] = v['sanctioned'] or bool(rec.get('sx'))
    return vessels, daily_counts, days


def build_period_report(acc, start_key, end_key, mid_table, period_fields,
                        max_vessels=REPORT_MAX_VESSELS,
                        max_cells=HOTSPOT_MAX_CELLS):
    """組出週/月報的完整 JSON 結構（純函式）。"""
    vessels, daily_counts, days = bucket_range(acc, start_key, end_key)
    meta_all = acc.get('vessels', {})

    rows = []
    hotspot_events = []
    loiter_hours_total = 0.0
    for mmsi, v in vessels.items():
        meta = meta_all.get(mmsi, {})
        flag = flag_for_mmsi(mmsi, mid_table)
        evs = sorted(v['events'].values(), key=lambda e: -(e[2] or 0))
        # 事件時數加權均速；區間內無事件時退回滯留時數最大那天的均速
        sp_h = sum(e[2] for e in evs
                   if isinstance(e[3], (int, float)) and (e[2] or 0) > 0)
        sp_w = sum(e[3] * e[2] for e in evs
                   if isinstance(e[3], (int, float)) and (e[2] or 0) > 0)
        avg_kn = round(sp_w / sp_h, 1) if sp_h else v['loiter_kn_fallback']
        ev_hours = sum((e[2] or 0) for e in evs)
        loiter_hours_total += ev_hours
        for e in evs:
            hotspot_events.append({
                'lat': e[0], 'lon': e[1], 'mmsi': mmsi,
                'hours': e[2], 'avg_speed_kn': e[3]})
        voyage = meta.get('voyage') or {}
        vt = meta.get('type') or 'unknown'
        rows.append({
            'mmsi': mmsi,
            'name': meta.get('name') or '',
            'vessel_type': vt,
            'gov_category': vt if vt in GOV_TYPES else '',
            'flag_mid': flag['mid'],
            'flag_en': flag['en'],
            'flag_zh': flag['zh'],
            'max_risk_score': v['max_score'],
            'risk_level': v['risk_level'],
            'days_seen': v['days_seen'],
            'cable_loiter_hours': round(v['loiter_hours'], 1),
            'cable_loiter_avg_speed_kn': avg_kn,
            'cable_loiter_events': len(evs),
            'cables_nearby': sorted(v['cables']),
            'offshore_loiter_days': round(v['off_days'], 1),
            'non_top10_flag': v['non_top10'],
            'sanctioned': v['sanctioned'],
            'departure_port': voyage.get('departure_port'),
            'departure_time_utc': voyage.get('departure_time'),
            'arrival_port': voyage.get('arrival_port'),
            'arrival_time_utc': voyage.get('arrival_time'),
            'time_at_sea_hours': voyage.get('at_sea_hours'),
            'time_at_sea_note': ('未觀測到靠港'
                                 if voyage.get('observed_span_only') else ''),
            'last_lat': meta.get('last_lat'),
            'last_lon': meta.get('last_lon'),
            'last_zone': meta.get('zone'),
            'last_seen_utc': meta.get('last_seen'),
        })

    rows.sort(key=lambda r: (-r['max_risk_score'],
                             RISK_ORDER.get(r['risk_level'], 3), r['mmsi']))

    by_type = Counter(r['vessel_type'] for r in rows)
    by_flag = {}
    for r in rows:
        f = by_flag.setdefault(r['flag_mid'], {
            'en': r['flag_en'], 'zh': r['flag_zh'], 'count': 0})
        f['count'] += 1
    by_flag = dict(sorted(by_flag.items(), key=lambda kv: -kv[1]['count']))

    summary = {
        'unique_highrisk': len(rows),
        'critical': sum(1 for r in rows if r['risk_level'] == 'critical'),
        'high': sum(1 for r in rows if r['risk_level'] == 'high'),
        'cable_loiter_vessels': sum(
            1 for r in rows
            if r['cable_loiter_events'] > 0
            or r['cable_loiter_hours'] >= CABLE_LOITER_MIN_HOURS),
        'cable_loiter_hours_total': round(loiter_hours_total, 1),
        'offshore_loiter_vessels': sum(
            1 for r in rows
            if r['offshore_loiter_days'] >= OFFSHORE_LOITER_MIN_DAYS),
        'non_top10_flag_vessels': sum(1 for r in rows if r['non_top10_flag']),
        'sanctioned_vessels': sum(1 for r in rows if r['sanctioned']),
        'by_type': dict(by_type.most_common()),
        'by_flag': by_flag,
        'daily_counts': daily_counts,
    }

    report = dict(period_fields)
    report.update({
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'start': start_key,
        'end': end_key,
        'days_covered': len(days),
        'daily_cap': DAILY_MAX_ROWS,
        'vessel_detail_cap': max_vessels,
        'summary': summary,
        'hotspots': build_stat_grid(hotspot_events)[:max_cells],
        'vessels': rows[:max_vessels],
    })
    return report


# ══════════════════════════════════════════════════════════════════
# 期間計算 / gate
# ══════════════════════════════════════════════════════════════════

def previous_iso_week(today):
    """回傳 (label '2026-W35', 週一 date, 週日 date) — 上一個完整 ISO 週。"""
    monday_this = today - timedelta(days=today.weekday())
    start = monday_this - timedelta(days=7)
    end = start + timedelta(days=6)
    iso = start.isocalendar()
    return f'{iso[0]}-W{iso[1]:02d}', start, end


def previous_month(today):
    """回傳 (label '2026-08', 月首 date, 月末 date) — 上一個日曆月。"""
    first_this = today.replace(day=1)
    end = first_this - timedelta(days=1)
    start = end.replace(day=1)
    return start.strftime('%Y-%m'), start, end


def should_run_weekly(today, out_path, force=False):
    """週一~三且輸出檔不存在才跑（GitHub cron 延遲/漏跑需要 grace window，
    輸出檔存在 gate 讓補跑/重跑冪等）。"""
    if force:
        return True
    if today.weekday() not in WEEKLY_GRACE_WEEKDAYS:
        return False
    return not Path(out_path).exists()


def should_run_monthly(today, out_path, force=False):
    if force:
        return True
    if today.day > MONTHLY_GRACE_DAYS:
        return False
    return not Path(out_path).exists()


def iso_weeks_in_range(start, end):
    """區間內出現過的 ISO 週 → [(label, week_start, week_end), ...]（週界
    夾在區間內）。供月報的逐週 mini 摘要。"""
    weeks = []
    cur = start - timedelta(days=start.weekday())   # 該週週一
    while cur <= end:
        iso = cur.isocalendar()
        label = f'{iso[0]}-W{iso[1]:02d}'
        w_start = max(cur, start)
        w_end = min(cur + timedelta(days=6), end)
        weeks.append((label, w_start, w_end))
        cur += timedelta(days=7)
    return weeks


# ══════════════════════════════════════════════════════════════════
# 前端 manifest（docs/weekly-report.html 的期別清單）
# ══════════════════════════════════════════════════════════════════

def build_manifest_entry(report):
    """從完整報表抽出列表頁需要的輕量摘要（純函式）。"""
    s = report.get('summary') or {}
    entry = {
        'start': report.get('start'),
        'end': report.get('end'),
        'days_covered': report.get('days_covered'),
        'generated_at': report.get('generated_at'),
        'unique_highrisk': s.get('unique_highrisk'),
        'critical': s.get('critical'),
        'cable_loiter_vessels': s.get('cable_loiter_vessels'),
        'cable_loiter_hours_total': s.get('cable_loiter_hours_total'),
    }
    if report.get('week'):
        entry['week'] = report['week']
    if report.get('month'):
        entry['month'] = report['month']
    return entry


def write_manifest(weekly_dir=WEEKLY_DIR, monthly_dir=MONTHLY_DIR):
    """重掃兩個輸出目錄 → docs/reports/weekly/index.json（新→舊）。

    週報頁（docs/weekly-report.html）靠這個檔知道有哪些期別 — 靜態站
    沒有目錄列表。每次 weekly/monthly 產出後重建。
    """
    def collect(d, key):
        items = []
        for p in Path(d).glob('*.json'):
            if p.name == 'index.json':
                continue
            rep = load_json(p, None, expect_type=dict)
            if rep and rep.get(key):
                items.append(build_manifest_entry(rep))
        items.sort(key=lambda e: e.get(key) or '', reverse=True)
        return items

    manifest = {
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'weekly': collect(weekly_dir, 'week'),
        'monthly': collect(monthly_dir, 'month'),
    }
    out = Path(weekly_dir) / 'index.json'
    atomic_write_json(out, manifest)
    print(f'🗂️ manifest：週報 {len(manifest["weekly"])} 期 / '
          f'月報 {len(manifest["monthly"])} 期 → {out}')
    return manifest


# ══════════════════════════════════════════════════════════════════
# CSV
# ══════════════════════════════════════════════════════════════════

def write_report_csv(path, rows):
    """逐船明細 → CSV。utf-8-sig：Excel 直接開啟中文才不會亂碼。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction='ignore')
        w.writeheader()
        for r in rows:
            out = dict(r)
            out['cables_nearby'] = ';'.join(r.get('cables_nearby') or [])
            for k in ('non_top10_flag', 'sanctioned'):
                out[k] = 1 if r.get(k) else 0
            w.writerow(out)


# ══════════════════════════════════════════════════════════════════
# 執行模式
# ══════════════════════════════════════════════════════════════════

def _today(args):
    if args.date:
        return datetime.strptime(args.date, '%Y-%m-%d').date()
    return datetime.now(timezone.utc).date()


def run_accumulate(args):
    snap = load_json(SNAPSHOT_FILE, None, expect_type=dict)
    if not snap or not snap.get('vessels'):
        # 上游 analyze_suspicious 失敗/未跑：不 cascade，等下一輪
        print(f'⚠️ {SNAPSHOT_FILE} 不存在或為空，跳過累積（上游可能失敗）')
        return 0

    if args.date:
        date_key = args.date
    else:
        date_key = (str(snap.get('updated_at') or '')[:10]
                    or datetime.now(timezone.utc).strftime('%Y-%m-%d'))

    rows = select_daily_highrisk(snap['vessels'])
    print(f'📥 快照 {snap.get("count", len(snap["vessels"]))} 艘高風險'
          f' → 優先排序取 {len(rows)} 艘（{date_key}）')

    # 航程 enrichment：只對入選船跑（port 查表已有 100m 網格快取）
    metas = {}
    tracks = {}
    try:
        import geofence
        from analyze_suspicious import load_track_history
        tracks = load_track_history()
        port_lookup = geofence.is_in_port_cached
    except Exception as e:  # 缺航跡檔等 — voyage 全 null，其餘照常
        print(f'⚠️ 航跡載入失敗，本輪不做航程 enrichment: {e}')
        port_lookup = None
    for row in rows:
        mmsi = str(row.get('mmsi') or '')
        pts = tracks.get(mmsi) if tracks else None
        voyage = (segment_voyages(pts, port_lookup)
                  if pts and port_lookup else empty_voyage())
        metas[mmsi] = {
            'name': row.get('name') or '',
            'type': row.get('vessel_type') or 'unknown',
            'last_lat': row.get('last_lat'),
            'last_lon': row.get('last_lon'),
            'last_seen': row.get('last_seen'),
            'zone': row.get('zone'),
            'voyage': voyage,
        }

    acc = load_json(ACCUMULATOR_FILE, None, expect_type=dict)
    if not acc or not isinstance(acc.get('daily'), dict):
        acc = new_accumulator()
    merge_into_accumulator(acc, date_key, rows, metas)
    atomic_write_json(ACCUMULATOR_FILE, acc, compact=True)
    print(f'💾 累積檔更新：{len(acc["daily"])} 天 / '
          f'{len(acc["vessels"])} 艘 → {ACCUMULATOR_FILE}')
    return 0


def _load_accumulator_or_warn():
    acc = load_json(ACCUMULATOR_FILE, None, expect_type=dict)
    if not acc or not isinstance(acc.get('daily'), dict):
        print(f'⚠️ {ACCUMULATOR_FILE} 不存在 — 累積尚未開始（冷啟動）')
        return new_accumulator()
    return acc


def run_weekly(args):
    today = _today(args)
    label, start, end = previous_iso_week(today)
    out_json = WEEKLY_DIR / f'{label}.json'
    if not should_run_weekly(today, out_json, args.force):
        print(f'⏭️ 週報 gate 未達（今天 {today}，目標 {label}，'
              f'檔案存在={out_json.exists()}）— 用 --force 可強制')
        return 0

    acc = _load_accumulator_or_warn()
    report = build_period_report(
        acc, start.isoformat(), end.isoformat(), load_mid_flags(),
        {'period': 'weekly', 'week': label})
    if report['days_covered'] < 7:
        print(f'ℹ️ 該週累積僅 {report["days_covered"]} 天（冷啟動/漏跑）— 照常產出')
    atomic_write_json(out_json, report)
    out_csv = WEEKLY_DIR / f'{label}.csv'
    write_report_csv(out_csv, report['vessels'])
    write_manifest()
    print(f'📊 週報 {label}：{report["summary"]["unique_highrisk"]} 艘 / '
          f'熱區 {len(report["hotspots"])} 格 → {out_json} + {out_csv}')
    return 0


def month_trend_from_daily_reports(label, reports_dir=DAILY_REPORTS_DIR):
    """從既有日報系列（docs/reports/YYYY-MM-DD.json）取當月趨勢。"""
    sus, cable = [], []
    for p in sorted(Path(reports_dir).glob(f'{label}-*.json')):
        d = load_json(p, None, expect_type=dict)
        if not d:
            continue
        if isinstance(d.get('suspicious_count'), (int, float)):
            sus.append(d['suspicious_count'])
        if isinstance(d.get('cable_near_count'), (int, float)):
            cable.append(d['cable_near_count'])
    def stats(xs):
        if not xs:
            return None
        return {'avg': round(sum(xs) / len(xs), 1), 'max': max(xs),
                'days': len(xs)}
    return {'suspicious_count': stats(sus), 'cable_near_count': stats(cable)}


def run_monthly(args):
    today = _today(args)
    label, start, end = previous_month(today)
    out_json = MONTHLY_DIR / f'{label}.json'
    if not should_run_monthly(today, out_json, args.force):
        print(f'⏭️ 月報 gate 未達（今天 {today}，目標 {label}，'
              f'檔案存在={out_json.exists()}）— 用 --force 可強制')
        return 0

    acc = _load_accumulator_or_warn()
    report = build_period_report(
        acc, start.isoformat(), end.isoformat(), load_mid_flags(),
        {'period': 'monthly', 'month': label})

    # 逐週 mini 摘要（月報素材；一律從日列重算，不讀週檔 — ISO 週跨月界）
    weeks = {}
    for wlabel, w_start, w_end in iso_weeks_in_range(start, end):
        wv, _, wdays = bucket_range(acc, w_start.isoformat(),
                                    w_end.isoformat())
        weeks[wlabel] = {
            'start': w_start.isoformat(), 'end': w_end.isoformat(),
            'days_covered': len(wdays),
            'unique_highrisk': len(wv),
            'critical': sum(1 for v in wv.values()
                            if v['risk_level'] == 'critical'),
        }
    report['weeks'] = weeks
    report['trend'] = month_trend_from_daily_reports(label)

    atomic_write_json(out_json, report)
    out_csv = MONTHLY_DIR / f'{label}.csv'
    write_report_csv(out_csv, report['vessels'])
    write_manifest()
    print(f'📊 月報 {label}：{report["summary"]["unique_highrisk"]} 艘 / '
          f'{len(weeks)} 週 → {out_json} + {out_csv}')
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description='高風險船舶週/月彙整')
    ap.add_argument('--mode', required=True,
                    choices=['accumulate', 'weekly', 'monthly'])
    ap.add_argument('--date', help='覆寫「今天」(YYYY-MM-DD, UTC) — 測試/回填用')
    ap.add_argument('--force', action='store_true',
                    help='跳過日期與輸出檔存在 gate')
    args = ap.parse_args(argv)
    if args.mode == 'accumulate':
        return run_accumulate(args)
    if args.mode == 'weekly':
        return run_weekly(args)
    return run_monthly(args)


if __name__ == '__main__':
    sys.exit(main())
