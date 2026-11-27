"""Offline tests: no real credentials, network requests, or commercial positions."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
import requests

import fetch_coastguard_myshiptracking as ccg
from coastguard_private_store import PrivateStore, StoreError

NOW = datetime(2026, 11, 27, 12, tzinfo=timezone.utc)
TARGETS = [{'mmsi': '413875010', 'name': 'CCG1401', 'evidence': 'test'}]


def row(**kwargs):
    data = {'mmsi': 413875010, 'vessel_name': 'CCG1401', 'lat': 24.5, 'lng': 122,
            'received': NOW.isoformat(), 'speed': 10, 'course': 120, 'vtype': 10}
    return dict(data, **kwargs)


class Store:
    def __init__(self, claimed=True, latest=None):
        self.claimed = claimed
        self.latest = latest or {}
        self.results = []
        self.claims = []

    def claim(self, *args):
        self.claims.append(args)
        return {'claimed': self.claimed, 'latest': self.latest}

    def finish(self, *args):
        self.results.append(args)
        return {'stored': True}


def client():
    api = Mock()
    api.account.return_value = {'available_coins': 2000, 'expire_date': None}
    api.positions.return_value = ({'status': 'success', 'data': [row()]}, 1)
    return api


def response(code=200, data=None, cost='1', **headers):
    r = Mock()
    r.status_code = code
    r.ok = code < 400
    r.headers = headers
    if cost is not None:
        r.headers['X-Credit-Charged'] = cost
    r.json.return_value = data if data is not None else {'status': 'success', 'data': [row()]}
    return r


def test_seed_list_is_exact_and_unique():
    targets = ccg.load_watchlist()
    assert len(targets) == len({t['mmsi'] for t in targets}) == 14
    assert {'413875010', '413875052', '413875186'} <= {t['mmsi'] for t in targets}


@pytest.mark.parametrize('mmsi', ['413875', '416123456', 413875010, '123456789'])
def test_watchlist_rejects_unapproved_identity(tmp_path, mmsi):
    path = tmp_path / 'list.json'
    path.write_text(json.dumps({'vessels': [dict(TARGETS[0], mmsi=mmsi)]}))
    with pytest.raises(ValueError):
        ccg.load_watchlist(path)


def test_duplicate_watchlist_rejected(tmp_path):
    path = tmp_path / 'list.json'
    path.write_text(json.dumps({'vessels': TARGETS * 2}))
    with pytest.raises(ValueError):
        ccg.load_watchlist(path)


def test_normalization_preserves_provider_time_and_type_namespace():
    point = ccg.normalize(row(received=(NOW-timedelta(minutes=30)).isoformat(), course=511), NOW)
    assert point['mmsi'] == '413875010'
    assert point['fetched_at'] != point['received']
    assert point['provider_vtype'] == 10 and point['gov_type'] == 'coastguard'
    assert point['course'] is None


@pytest.mark.parametrize('bad', [dict(lat=91), dict(lng=181), dict(lat=0, lng=0),
    dict(lat=float('nan')), dict(lat=True), dict(received='2026-11-27T12:00:00'),
    dict(received=(NOW+timedelta(minutes=6)).isoformat())])
def test_invalid_positions_quarantined(bad):
    result = ccg.build_result(TARGETS, {'data': [row(**bad)]}, {}, NOW)
    assert result['observations'] == []
    assert result['targets'][0]['query_status'] == 'invalid'


@pytest.mark.parametrize('hours,expected', [(0, 'fresh'), (1, 'fresh'), (2, 'delayed'),
                                          (6, 'delayed'), (7, 'stale')])
def test_freshness_uses_observation_time(hours, expected):
    assert ccg.freshness({'received': (NOW-timedelta(hours=hours)).isoformat()}, NOW) == expected


def test_missing_result_preserves_original_timestamp():
    old = ccg.normalize(row(received=(NOW-timedelta(hours=20)).isoformat()), NOW)
    result = ccg.build_result(TARGETS, {'data': []}, {'413875010': old}, NOW)
    state = result['targets'][0]
    assert state['query_status'] == 'not_returned' and state['freshness'] == 'stale'
    assert state['last_position'] == old


def test_older_return_does_not_replace_latest():
    latest = ccg.normalize(row(), NOW)
    old = row(received=(NOW-timedelta(hours=10)).isoformat(), lat=25)
    result = ccg.build_result(TARGETS, {'data': [old]}, {'413875010': latest}, NOW)
    assert result['targets'][0]['last_position'] == latest


def test_foreign_mmsi_is_not_added_to_watchlist():
    result = ccg.build_result(TARGETS, {'data': [row(mmsi=416123456)]}, {}, NOW)
    assert result['observations'] == []


def test_latest_duplicate_row_selected_and_stationary_points_valid():
    earlier = row(received=(NOW-timedelta(hours=1)).isoformat())
    result = ccg.build_result(TARGETS, {'data': [row(), earlier]}, {}, NOW)
    assert len(result['observations']) == 1
    assert result['observations'][0]['received'] == NOW.isoformat()


def test_paid_calls_require_atomic_claim():
    api, store = client(), Store(claimed=False)
    assert ccg.track(api, store, TARGETS, NOW, 'test') == 'skipped'
    api.account.assert_not_called()
    api.positions.assert_not_called()


def test_complete_run_only_writes_private_store():
    api, store = client(), Store()
    assert ccg.track(api, store, TARGETS, NOW, 'test') == 'success'
    assert store.claims == [('test', 1, 100)]
    assert store.results[0][2] == 1
    assert api.account.call_count == 2


@pytest.mark.parametrize('account', [dict(available_coins=100),
    dict(available_coins=1000, expire_date='2026-11-26T00:00:00Z')])
def test_no_paid_request_on_low_balance_or_expiry(account):
    api, store = client(), Store()
    api.account.return_value = account
    assert ccg.track(api, store, TARGETS, NOW, 'test') == 'budget_or_expiry_stop'
    api.positions.assert_not_called()
    assert store.results[0][2] == 0


def test_unknown_billing_retains_reservation_and_is_not_retried():
    api, store = client(), Store()
    api.positions.side_effect = ccg.ProviderError('NETWORK_RESULT_UNKNOWN')
    with pytest.raises(ccg.ProviderError):
        ccg.track(api, store, TARGETS, NOW, 'test')
    assert store.results[0][2] is None
    api.positions.assert_called_once()


def test_malformed_success_still_records_known_charge():
    api, store = client(), Store()
    api.positions.return_value = ({'data': None}, 1)
    with pytest.raises(ccg.ProviderError):
        ccg.track(api, store, TARGETS, NOW, 'test')
    assert store.results[0][2] == 1


def test_account_after_failure_does_not_discard_paid_result():
    api, store = client(), Store()
    api.account.side_effect = [{'available_coins': 2000}, ccg.ProviderError('API_ERROR')]
    assert ccg.track(api, store, TARGETS, NOW, 'test') == 'success'
    assert store.results[0][1]['account_after_unavailable']


def test_secrets_redacted_from_private_raw_response():
    api, store = client(), Store()
    api.positions.return_value = ({'status': 'success', 'data': [row()],
                                   'echo': 'your_token_here'}, 1)
    ccg.track(api, store, TARGETS, NOW, 'test', secret_values=('your_token_here',))
    assert 'your_token_here' not in json.dumps(store.results)


def test_fail_closed_when_budget_insufficient():
    api, store = client(), Store()
    with pytest.raises(ValueError):
        ccg.track(api, store, TARGETS, NOW, 'test', run_limit=0)
    assert store.claims == []


@pytest.mark.parametrize('status', [401, 402, 403, 500])
def test_http_errors_are_not_replayed(status):
    session = Mock()
    session.get.return_value = response(status, {'status': 'error', 'code': 'ERR_NOACCESS'}, cost=None)
    with pytest.raises(ccg.ProviderError):
        ccg.Client('your_token_here', session).positions(TARGETS)
    session.get.assert_called_once()


def test_network_exception_does_not_leak_request_headers():
    session = Mock()
    session.get.side_effect = requests.ConnectionError('your_token_here')
    with pytest.raises(ccg.ProviderError) as exc:
        ccg.Client('your_token_here', session).positions(TARGETS)
    assert 'your_token_here' not in str(exc.value)
    session.get.assert_called_once()


def test_rate_limit_backoff_is_bounded():
    session, sleep = Mock(), Mock()
    session.get.side_effect = [response(429, cost='0', **{'Retry-After': '1'})] * 3
    with pytest.raises(ccg.ProviderError):
        ccg.Client('your_token_here', session, sleep).positions(TARGETS)
    assert session.get.call_count == 3 and sleep.call_count == 2


def test_unbounded_retry_after_is_not_ignored():
    session, sleep = Mock(), Mock()
    session.get.return_value = response(429, cost='0', **{'Retry-After': '3600'})
    with pytest.raises(ccg.ProviderError):
        ccg.Client('your_token_here', session, sleep).positions(TARGETS)
    session.get.assert_called_once()
    sleep.assert_not_called()


def test_bulk_auth_uses_header_and_one_credit_per_target_not_call():
    session = Mock()
    session.get.return_value = response()
    ccg.Client('your_token_here', session).positions(TARGETS)
    args, kwargs = session.get.call_args
    assert args[0] == ccg.API + 'vessel/bulk'
    assert kwargs['headers'] == {'x-api-key': 'your_token_here'}
    assert kwargs['params']['response'] == 'simple' and kwargs['params']['src'] == 'ter'


def test_all_vessels_not_found_preserves_per_target_states():
    session = Mock()
    session.get.return_value = response(404, {'status': 'error', 'code': 'ERR_VESSELS_NOT_FOUND'}, cost='0')
    payload, cost = ccg.Client('your_token_here', session).positions(TARGETS)
    result = ccg.build_result(TARGETS, payload, {}, NOW)
    assert cost == 0 and result['targets'][0]['query_status'] == 'not_returned'


def test_private_store_errors_never_echo_service_key():
    session = Mock()
    session.post.side_effect = requests.ConnectionError('your_service_key_here')
    with pytest.raises(StoreError) as exc:
        PrivateStore('https://example.supabase.co', 'your_service_key_here', session).claim('test', 1, 100)
    assert 'your_service_key_here' not in str(exc.value)


@pytest.mark.parametrize('url', ['http://example.com', 'https://user:pass@example.com',
                                'https://example.com/path', 'https://example.com?key=test'])
def test_private_store_rejects_unsafe_url(url):
    with pytest.raises(StoreError):
        PrivateStore(url, 'your_service_key_here')


def test_dry_run_requires_no_secrets_or_network(monkeypatch, capsys):
    monkeypatch.setattr(requests.Session, 'get', Mock(side_effect=AssertionError('network')))
    assert ccg.main(['--dry-run']) == 0
    assert '14 configured' in capsys.readouterr().out


def test_disabled_live_run_is_noop(monkeypatch):
    monkeypatch.delenv('MST_TRACKING_ENABLED', raising=False)
    monkeypatch.setattr(ccg, 'Client', Mock(side_effect=AssertionError('client')))
    assert ccg.main(['--live']) == 0


def test_storage_approval_required_before_paid_calls(monkeypatch):
    monkeypatch.setenv('MST_TRACKING_ENABLED', 'true')
    monkeypatch.delenv('MST_STORAGE_READY', raising=False)
    monkeypatch.setattr(ccg, 'Client', Mock(side_effect=AssertionError('client')))
    assert ccg.main(['--live']) == 1
