#!/usr/bin/env python3
"""Supabase (PostgREST) 儲存層：逐船航跡 vessel_routes。

為什麼存在：逐船航跡有 3 萬檔，塞進 main 會讓 repo 爆炸、放進 Pages
artifact 會讓部署逾時，所以原本用「單一 commit force-push 到 vessel-data
分支」的變通法。改存 Supabase 後前端可用 MMSI 直接點查（單次 ~10-50KB），
公務船繪圖也能用 type 篩選，不必掃全部 3 萬檔。

設定（未設定時所有函式安全 no-op，pipeline 退回本地檔案 / vessel-data 分支）：
  SUPABASE_URL          專案 URL，例：https://xxxx.supabase.co
  SUPABASE_SERVICE_KEY  service_role key（寫入用，繞過 RLS）— 僅供 CI，勿外流
  SUPABASE_ANON_KEY     publishable/anon key（唯讀，可公開）

僅依賴 stdlib + requests（update-ais.yml 環境只裝 requests + pysocks）。
"""
import os

from io_utils import make_retry_session

TABLE = 'vessel_routes'

# 每批 upsert 的列數。航跡 payload 大（單船最多數百點），批次過大會撞
# PostgREST 的 request body 上限並拉長單次重試成本。
UPSERT_BATCH = 200

# Supabase 走一般 HTTPS，不應套用 AIS 專用的 SOCKS5 proxy（那個 proxy 只
# 通往港務局），故所有請求明確關閉環境變數帶進來的 proxy 設定。
_NO_PROXY = {'http': None, 'https': None}

_session = None


def _get_session():
    global _session
    if _session is None:
        # upsert 用 on-conflict merge，重送同一批結果相同 → POST retry 安全。
        _session = make_retry_session()
    return _session


def base_url():
    """回傳 PostgREST base URL（未設定回 None）。"""
    url = (os.environ.get('SUPABASE_URL') or '').strip().rstrip('/')
    return url or None


def _key(write):
    if write:
        return (os.environ.get('SUPABASE_SERVICE_KEY') or '').strip() or None
    return ((os.environ.get('SUPABASE_ANON_KEY') or '').strip()
            or (os.environ.get('SUPABASE_SERVICE_KEY') or '').strip() or None)


def is_configured(write=False):
    """是否具備連線條件。write=True 時要求 service_role key。"""
    return bool(base_url() and _key(write))


def _headers(write):
    key = _key(write)
    return {
        'apikey': key,
        'Authorization': f'Bearer {key}',
        'Content-Type': 'application/json',
    }


def _endpoint():
    return f'{base_url()}/rest/v1/{TABLE}'


def route_row(mmsi, name, imo, flag, vtype, track):
    """把 extract_all_routes 的輸出轉成一列 vessel_routes。

    track 已依時間排序，故首末點即 first_seen / last_seen。
    """
    first = track[0].get('t') if track else None
    last = track[-1].get('t') if track else None
    return {
        'mmsi': str(mmsi),
        'name': name or '',
        'imo': imo or '',
        'flag': flag or '',
        'type': vtype or '',
        'point_count': len(track),
        'first_seen': first or None,
        'last_seen': last or None,
        'track': track,
    }


def upsert_routes(rows, *, run_started_at=None, batch_size=UPSERT_BATCH):
    """分批 upsert 航跡列；run_started_at 有值時刪除本輪未更新的過期船。

    回傳 (upserted, deleted)。任一批失敗會 raise，交由呼叫端決定是否致命。
    """
    if not rows:
        return 0, 0
    if not is_configured(write=True):
        raise RuntimeError('SUPABASE_URL / SUPABASE_SERVICE_KEY 未設定')

    session = _get_session()
    headers = dict(_headers(write=True))
    # merge-duplicates = ON CONFLICT (mmsi) DO UPDATE；minimal 讓回應不帶 body，
    # 免得 3 萬列的回聲吃掉 egress 額度。
    headers['Prefer'] = 'resolution=merge-duplicates,return=minimal'

    upserted = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        resp = session.post(_endpoint(), json=batch, headers=headers,
                            timeout=120, proxies=_NO_PROXY)
        if resp.status_code >= 400:
            raise RuntimeError(
                f'vessel_routes upsert 失敗 (HTTP {resp.status_code}): '
                f'{resp.text[:300]}')
        upserted += len(batch)

    deleted = 0
    if run_started_at:
        deleted = _delete_stale(session, run_started_at)
    return upserted, deleted


def _delete_stale(session, run_started_at):
    """刪掉本輪沒被 upsert 到的列。

    updated_at 有 DB default now()，每次 upsert 都會被觸發改寫；因此
    updated_at < 本輪開始時間 == 這艘船已離開保留窗口。比先撈 3 萬個 mmsi
    再比對省下一整趟下載流量。回傳刪除列數（Prefer: count=exact）。
    """
    headers = dict(_headers(write=True))
    headers['Prefer'] = 'return=minimal,count=exact'
    resp = session.delete(f'{_endpoint()}?updated_at=lt.{run_started_at}',
                          headers=headers, timeout=120, proxies=_NO_PROXY)
    if resp.status_code >= 400:
        raise RuntimeError(
            f'vessel_routes 清除過期列失敗 (HTTP {resp.status_code}): '
            f'{resp.text[:300]}')
    return _parse_content_range(resp.headers.get('Content-Range'))


def _parse_content_range(value):
    """PostgREST count=exact 的 Content-Range 形如 '0-4/5'；取總數。"""
    if not value or '/' not in value:
        return 0
    total = value.rsplit('/', 1)[1]
    return int(total) if total.isdigit() else 0


def fetch_route(mmsi):
    """點查單艘船航跡，回傳與本地 route JSON 同結構的 dict（查無回 None）。"""
    if not is_configured():
        return None
    try:
        resp = _get_session().get(
            _endpoint(),
            params={'mmsi': f'eq.{mmsi}', 'select': '*', 'limit': 1},
            headers=_headers(write=False), timeout=30, proxies=_NO_PROXY)
        if resp.status_code >= 400:
            print(f'⚠️ Supabase 航跡查詢失敗 (HTTP {resp.status_code}) mmsi={mmsi}')
            return None
        rows = resp.json()
    except Exception as e:  # 網路/JSON 皆退回呼叫端的本地來源
        print(f'⚠️ Supabase 航跡查詢例外 mmsi={mmsi}: {e}')
        return None
    return rows[0] if rows else None


def fetch_routes_by_type(types, *, page_size=500):
    """依 type 批次取航跡（公務船繪圖用），分頁避免單次回應過大。

    這是取代「掃 3 萬個本地檔再逐一 classify」的關鍵路徑：公務/科研船只有
    數十艘，用 type 過濾後下載量從數十 MB 降到數百 KB。
    """
    if not is_configured() or not types:
        return []
    in_list = ','.join(f'"{t}"' for t in types)
    out = []
    offset = 0
    session = _get_session()
    while True:
        headers = dict(_headers(write=False))
        headers['Range-Unit'] = 'items'
        headers['Range'] = f'{offset}-{offset + page_size - 1}'
        try:
            resp = session.get(_endpoint(),
                               params={'type': f'in.({in_list})', 'select': '*'},
                               headers=headers, timeout=60, proxies=_NO_PROXY)
            if resp.status_code >= 400:
                print(f'⚠️ Supabase 航跡批次查詢失敗 (HTTP {resp.status_code})')
                break
            rows = resp.json()
        except Exception as e:
            print(f'⚠️ Supabase 航跡批次查詢例外: {e}')
            break
        out.extend(rows)
        if len(rows) < page_size:
            break
        offset += page_size
    return out
