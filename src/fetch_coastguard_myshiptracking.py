"""Six-hour CCG observations, private storage only, dry-run by default."""
import argparse
import json
import math
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

from coastguard_private_store import PrivateStore, StoreError

API = 'https://api.myshiptracking.com/api/v2/'
SOURCE = 'myshiptracking_terrestrial'
WATCHLIST = Path(__file__).resolve().parents[1] / 'config/coastguard_watchlist.json'


class ProviderError(RuntimeError):
    def __init__(self, code, charged=None):
        super().__init__('MyShipTracking request failed')
        self.code = code if re.fullmatch(r'[A-Z0-9_]{1,64}', str(code)) else 'API_ERROR'
        self.charged = charged


def parse_time(value):
    dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('Timestamp requires a timezone')
    return dt.astimezone(timezone.utc)


def finite(value):
    if isinstance(value, bool) or value is None:
        raise ValueError('Invalid number')
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('Non-finite number')
    return result


def optional_number(value, lower, upper):
    try:
        number = finite(value)
        return number if lower <= number <= upper else None
    except (TypeError, ValueError):
        return None


def load_watchlist(path=WATCHLIST):
    items = json.loads(Path(path).read_text(encoding='utf-8'))['vessels']
    seen = set()
    for item in items:
        mmsi = item.get('mmsi')
        if (not isinstance(mmsi, str) or not re.fullmatch(r'\d{9}', mmsi)
                or not mmsi.startswith(('412', '413', '414')) or mmsi in seen
                or not item.get('evidence')):
            raise ValueError('Invalid or duplicate watchlist identity')
        seen.add(mmsi)
    if not items:
        raise ValueError('Empty watchlist')
    return items


def normalize(row, now):
    lat, lon = finite(row.get('lat')), finite(row.get('lng'))
    received = parse_time(row.get('received'))
    if not -90 <= lat <= 90 or not -180 <= lon <= 180 or (lat == 0 and lon == 0):
        raise ValueError('Invalid or masked coordinates')
    if (received - now).total_seconds() > 300:
        raise ValueError('Future observation')
    return {'source': SOURCE, 'mmsi': str(row['mmsi']),
            'provider_name': row.get('vessel_name'), 'gov_type': 'coastguard',
            'lat': lat, 'lon': lon, 'received': received.isoformat(),
            'fetched_at': now.isoformat(),
            'speed': optional_number(row.get('speed'), 0, 102.2),
            'course': optional_number(row.get('course'), 0, 359.9),
            'provider_vtype': row.get('vtype'),
            'in_monitoring_area': 19 <= lat <= 30 and 116 <= lon <= 130}


def freshness(position, now):
    if not position:
        return None
    age = (now - parse_time(position['received'])).total_seconds()
    return 'fresh' if age <= 3600 else 'delayed' if age <= 21600 else 'stale'


def build_result(targets, payload, cached, now):
    data = payload.get('data')
    if not isinstance(data, list):
        raise ProviderError('INVALID_RESPONSE')
    wanted = {v['mmsi'] for v in targets}
    returned, invalid = {}, set()
    for row in data:
        if not isinstance(row, dict):
            continue
        mmsi = str(row.get('mmsi'))
        if mmsi not in wanted:
            continue
        try:
            point = normalize(row, now)
            if mmsi not in returned or point['received'] > returned[mmsi]['received']:
                returned[mmsi] = point
        except (KeyError, TypeError, ValueError):
            invalid.add(mmsi)
    states = []
    for target in targets:
        mmsi = target['mmsi']
        point, old = returned.get(mmsi), cached.get(mmsi)
        latest = point
        if old and (not point or parse_time(old['received']) >= parse_time(point['received'])):
            latest = old
        states.append({'mmsi': mmsi, 'query_status': 'returned' if point else
                       'invalid' if mmsi in invalid else 'not_returned',
                       'freshness': freshness(latest, now), 'last_position': latest})
    return {'source': SOURCE, 'queried_at': now.isoformat(),
            'status': 'success' if len(returned) == len(targets) else 'partial_success',
            'observations': list(returned.values()), 'targets': states,
            'raw_response': payload}


def redact(value, secrets):
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, '[REDACTED]')
        return value
    if isinstance(value, list):
        return [redact(v, secrets) for v in value]
    if isinstance(value, dict):
        return {redact(k, secrets): redact(v, secrets) for k, v in value.items()}
    return value


class Client:
    def __init__(self, key, session=None, sleep=time.sleep):
        self.key = key
        self.session = session or requests.Session()
        self.sleep = sleep

    def get(self, path, params=None):
        for attempt in range(3):
            try:
                r = self.session.get(API + path, params=params,
                                     headers={'x-api-key': self.key}, timeout=(10, 45))
            except requests.RequestException:
                # A timed-out request may already have consumed credits. Never replay it.
                raise ProviderError('NETWORK_RESULT_UNKNOWN') from None
            try:
                cost = int(r.headers['X-Credit-Charged'])
                if cost < 0:
                    cost = None
            except (KeyError, ValueError, TypeError):
                cost = None
            if r.status_code == 429 and attempt < 2 and cost in (None, 0):
                try:
                    delay = float(r.headers.get('Retry-After', '5'))
                except ValueError:
                    delay = 5
                if not math.isfinite(delay) or not 0 <= delay <= 30:
                    raise ProviderError('RATE_LIMIT', cost)
                self.sleep(max(1, delay))
                continue
            try:
                payload = r.json()
            except ValueError:
                raise ProviderError('NON_JSON_RESPONSE', cost) from None
            if not isinstance(payload, dict):
                raise ProviderError('INVALID_RESPONSE', cost)
            if not r.ok or payload.get('status') != 'success':
                raise ProviderError(payload.get('code', 'API_ERROR'), cost)
            return payload, cost
        raise ProviderError('RATE_LIMIT')

    def account(self):
        payload, _ = self.get('account/info')
        data = payload.get('data')
        if not isinstance(data, dict):
            raise ProviderError('INVALID_ACCOUNT_RESPONSE')
        return data

    def positions(self, targets):
        try:
            return self.get('vessel/bulk', {'mmsi': ','.join(t['mmsi'] for t in targets),
                                          'response': 'simple', 'src': 'ter'})
        except ProviderError as exc:
            if exc.code == 'ERR_VESSELS_NOT_FOUND':
                return {'status': 'success', 'data': [], 'provider_notice': exc.code}, exc.charged
            raise


def track(client, store, targets, now, run_id, *, run_limit=25, daily_limit=100,
          reserve=100, secret_values=()):
    if not 1 <= len(targets) <= min(run_limit, 100) or daily_limit < len(targets) or reserve < 0:
        raise ValueError('Tracking budget cannot cover the complete watchlist')
    claim = store.claim(run_id, len(targets), daily_limit)
    if not claim.get('claimed'):
        return 'skipped'
    charged = 0
    result = {'status': 'error', 'observations': [], 'queried_at': now.isoformat()}
    api_error = False
    try:
        account = client.account()
        balance = int(account['available_coins'])
        expires = account.get('expire_date')
        if (expires and parse_time(expires) <= now) or balance < len(targets) + reserve:
            result['status'] = 'budget_or_expiry_stop'
        else:
            # Reserve the full batch until billing is known, including on timeout/crash.
            charged = None
            payload, charged = client.positions(targets)
            result = build_result(targets, payload, claim.get('latest', {}), now)
            result['balance_before'] = balance
            try:
                after = client.account()
                result['balance_after'] = int(after['available_coins'])
            except (ProviderError, KeyError, TypeError, ValueError):
                result['account_after_unavailable'] = True
    except ProviderError as exc:
        api_error = True
        if charged is None:
            charged = exc.charged
        result.update(status='error', code=exc.code)
    except (KeyError, TypeError, ValueError):
        api_error = True
        result.update(status='error', code='INVALID_RESPONSE')
    if charged is not None and not 0 <= charged <= len(targets):
        result['billing_anomaly'] = True
        api_error = True
    result['credits_charged'] = charged
    stored = store.finish(run_id, redact(result, secret_values), charged)
    if not stored.get('stored'):
        raise StoreError('Private result was not acknowledged')
    if claim.get('overdue'):
        print('::warning::Private observation freshness overdue; inspect the private monitor.')
    if api_error:
        raise ProviderError('TRACKING_FAILED')
    return result['status']


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument('--live', action='store_true')
    mode.add_argument('--dry-run', action='store_true')
    ap.add_argument('--limit', type=int)
    args = ap.parse_args(argv)
    try:
        targets = load_watchlist()
        if args.limit is not None:
            if args.limit < 1:
                raise ValueError('Invalid limit')
            targets = targets[:args.limit]
        if not args.live:
            print(f'Dry-run: {len(targets)} configured targets, <= {len(targets)} credits/run; no API calls.')
            return 0
        if os.environ.get('MST_TRACKING_ENABLED', '').lower() != 'true':
            print('Tracking disabled; no API calls.')
            return 0
        if os.environ.get('MST_STORAGE_READY', '').lower() != 'true':
            raise StoreError('Private storage has not been approved')
        key = os.environ.get('MST_API_KEY', '')
        if not key:
            raise ValueError('Missing API credential')
        service_key = os.environ.get('SUPABASE_SERVICE_KEY', '')
        store = PrivateStore(os.environ.get('SUPABASE_URL', ''), service_key)
        status = track(Client(key), store, targets, datetime.now(timezone.utc),
                       os.environ.get('GITHUB_RUN_ID', str(uuid.uuid4())),
                       run_limit=int(os.environ.get('MST_MAX_CREDITS_PER_RUN', '25')),
                       daily_limit=int(os.environ.get('MST_DAILY_CREDIT_LIMIT', '100')),
                       reserve=int(os.environ.get('MST_MIN_BALANCE_RESERVE', '100')),
                       secret_values=(key, service_key))
        print('Private tracking job finished; no position data is published.')
        if status == 'partial_success':
            print('::warning::Incomplete observations; inspect the private run record.')
        return 0
    except (StoreError, ProviderError, ValueError, TypeError, OSError, KeyError):
        print('::error::Private tracking unavailable; check private configuration or account. No public data written.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
