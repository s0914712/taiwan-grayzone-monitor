"""supabase_store 的純函式 + PostgREST 請求組裝測試（不觸網）。"""
import json

import pytest

import supabase_store


class FakeResponse:
    def __init__(self, status_code=201, body=None, headers=None):
        self.status_code = status_code
        self._body = body if body is not None else []
        self.headers = headers or {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


class FakeSession:
    """記錄所有請求，並依序吐出預先排好的回應。"""

    def __init__(self, responses=None):
        self.calls = []
        self._responses = list(responses or [])

    def _next(self):
        return self._responses.pop(0) if self._responses else FakeResponse()

    def post(self, url, **kw):
        self.calls.append(('POST', url, kw))
        return self._next()

    def delete(self, url, **kw):
        self.calls.append(('DELETE', url, kw))
        return self._next()

    def get(self, url, **kw):
        self.calls.append(('GET', url, kw))
        return self._next()


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv('SUPABASE_URL', 'https://proj.supabase.co/')
    monkeypatch.setenv('SUPABASE_SERVICE_KEY', 'service-key')
    monkeypatch.delenv('SUPABASE_ANON_KEY', raising=False)


@pytest.fixture
def fake_session(monkeypatch):
    session = FakeSession()
    monkeypatch.setattr(supabase_store, '_get_session', lambda: session)
    return session


def test_not_configured_without_env(monkeypatch):
    monkeypatch.delenv('SUPABASE_URL', raising=False)
    monkeypatch.delenv('SUPABASE_SERVICE_KEY', raising=False)
    monkeypatch.delenv('SUPABASE_ANON_KEY', raising=False)
    assert supabase_store.is_configured() is False
    assert supabase_store.is_configured(write=True) is False
    # 未設定時讀取路徑安全 no-op，呼叫端才能退回本地檔案
    assert supabase_store.fetch_route('412446229') is None
    assert supabase_store.fetch_routes_by_type(['coastguard']) == []


def test_anon_key_is_not_enough_for_writes(monkeypatch):
    monkeypatch.setenv('SUPABASE_URL', 'https://proj.supabase.co')
    monkeypatch.setenv('SUPABASE_ANON_KEY', 'anon-key')
    monkeypatch.delenv('SUPABASE_SERVICE_KEY', raising=False)
    assert supabase_store.is_configured() is True
    assert supabase_store.is_configured(write=True) is False


def test_base_url_strips_trailing_slash(configured):
    assert supabase_store.base_url() == 'https://proj.supabase.co'


def test_route_row_derives_span_from_sorted_track():
    track = [
        {'t': '2026-08-20T00:00:00+00:00', 'lat': 24.1, 'lon': 118.0},
        {'t': '2026-08-21T00:00:00+00:00', 'lat': 24.2, 'lon': 118.1},
        {'t': '2026-08-22T00:00:00+00:00', 'lat': 24.3, 'lon': 118.2},
    ]
    row = supabase_store.route_row(412446229, 'MINSHAYU71052', '', '', 'fishing', track)
    assert row['mmsi'] == '412446229'  # 數字 MMSI 轉字串，對齊 text 主鍵
    assert row['point_count'] == 3
    assert row['first_seen'] == '2026-08-20T00:00:00+00:00'
    assert row['last_seen'] == '2026-08-22T00:00:00+00:00'
    assert row['track'] is track


def test_route_row_empty_track_has_null_span():
    row = supabase_store.route_row('1', '', '', '', '', [])
    assert row['point_count'] == 0
    assert row['first_seen'] is None and row['last_seen'] is None


def test_upsert_batches_and_uses_merge_duplicates(configured, fake_session):
    rows = [supabase_store.route_row(str(i), 'V', '', '', 'cargo', [])
            for i in range(5)]
    upserted, deleted = supabase_store.upsert_routes(rows, batch_size=2)

    assert (upserted, deleted) == (5, 0)
    posts = [c for c in fake_session.calls if c[0] == 'POST']
    assert [len(c[2]['json']) for c in posts] == [2, 2, 1]
    assert posts[0][1] == 'https://proj.supabase.co/rest/v1/vessel_routes'
    prefer = posts[0][2]['headers']['Prefer']
    assert 'resolution=merge-duplicates' in prefer
    # return=minimal：3 萬列的回聲會白白吃掉 egress 額度
    assert 'return=minimal' in prefer
    assert posts[0][2]['headers']['apikey'] == 'service-key'


def test_upsert_skips_ais_socks_proxy(configured, fake_session):
    supabase_store.upsert_routes([supabase_store.route_row('1', '', '', '', '', [])])
    post = next(c for c in fake_session.calls if c[0] == 'POST')
    assert post[2]['proxies'] == {'http': None, 'https': None}


def test_upsert_empty_is_noop_without_credentials(monkeypatch):
    monkeypatch.delenv('SUPABASE_URL', raising=False)
    monkeypatch.delenv('SUPABASE_SERVICE_KEY', raising=False)
    assert supabase_store.upsert_routes([]) == (0, 0)


def test_upsert_without_service_key_raises(monkeypatch):
    monkeypatch.delenv('SUPABASE_URL', raising=False)
    monkeypatch.delenv('SUPABASE_SERVICE_KEY', raising=False)
    with pytest.raises(RuntimeError, match='SUPABASE_URL'):
        supabase_store.upsert_routes([{'mmsi': '1'}])


def test_upsert_raises_on_http_error(configured, monkeypatch):
    session = FakeSession([FakeResponse(status_code=413, body={'msg': 'too big'})])
    monkeypatch.setattr(supabase_store, '_get_session', lambda: session)
    with pytest.raises(RuntimeError, match='413'):
        supabase_store.upsert_routes([{'mmsi': '1'}])


def test_stale_delete_filters_on_run_start(configured, monkeypatch):
    session = FakeSession([
        FakeResponse(status_code=201),
        FakeResponse(status_code=200, headers={'Content-Range': '*/7'}),
    ])
    monkeypatch.setattr(supabase_store, '_get_session', lambda: session)
    upserted, deleted = supabase_store.upsert_routes(
        [{'mmsi': '1'}], run_started_at='2026-08-23T12:00:00+00:00')

    assert (upserted, deleted) == (1, 7)
    delete = next(c for c in session.calls if c[0] == 'DELETE')
    # 沒被本輪 upsert 到的列 updated_at 仍停在上一輪 → 已離開保留窗口
    assert 'updated_at=lt.2026-08-23T12:00:00+00:00' in delete[1]
    assert 'count=exact' in delete[2]['headers']['Prefer']


def test_no_delete_without_run_start(configured, fake_session):
    supabase_store.upsert_routes([{'mmsi': '1'}])
    assert not [c for c in fake_session.calls if c[0] == 'DELETE']


@pytest.mark.parametrize('header,expected', [
    ('0-6/7', 7), ('*/0', 0), ('*/*', 0), (None, 0), ('garbage', 0),
])
def test_parse_content_range(header, expected):
    assert supabase_store._parse_content_range(header) == expected


def test_fetch_route_returns_single_row(configured, monkeypatch):
    row = {'mmsi': '412446229', 'track': [{'t': 'x', 'lat': 1, 'lon': 2}]}
    session = FakeSession([FakeResponse(status_code=200, body=[row])])
    monkeypatch.setattr(supabase_store, '_get_session', lambda: session)

    assert supabase_store.fetch_route('412446229') == row
    get = session.calls[0]
    assert get[2]['params']['mmsi'] == 'eq.412446229'


def test_fetch_route_missing_returns_none(configured, monkeypatch):
    session = FakeSession([FakeResponse(status_code=200, body=[])])
    monkeypatch.setattr(supabase_store, '_get_session', lambda: session)
    assert supabase_store.fetch_route('000000000') is None


def test_fetch_route_swallows_network_error(configured, monkeypatch):
    class Boom(FakeSession):
        def get(self, url, **kw):
            raise OSError('connection reset')
    monkeypatch.setattr(supabase_store, '_get_session', lambda: Boom())
    # 網路錯誤必須讓呼叫端退回本地檔案，而不是炸掉整條 pipeline
    assert supabase_store.fetch_route('412446229') is None


def test_fetch_routes_by_type_pages_until_short_page(configured, monkeypatch):
    page1 = [{'mmsi': str(i), 'type': 'coastguard'} for i in range(2)]
    page2 = [{'mmsi': '99', 'type': 'research'}]
    session = FakeSession([FakeResponse(status_code=200, body=page1),
                           FakeResponse(status_code=200, body=page2)])
    monkeypatch.setattr(supabase_store, '_get_session', lambda: session)

    rows = supabase_store.fetch_routes_by_type(['coastguard', 'research'],
                                               page_size=2)
    assert [r['mmsi'] for r in rows] == ['0', '1', '99']
    ranges = [c[2]['headers']['Range'] for c in session.calls]
    assert ranges == ['0-1', '2-3']
    assert session.calls[0][2]['params']['type'] == 'in.("coastguard","research")'


def test_fetch_routes_by_type_empty_types(configured, fake_session):
    assert supabase_store.fetch_routes_by_type([]) == []
    assert fake_session.calls == []
