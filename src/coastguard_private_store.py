"""Service-role-only RPC client. Never writes commercial positions to the repo."""
from urllib.parse import urlsplit

import requests


class StoreError(RuntimeError):
    pass


class PrivateStore:
    def __init__(self, url, key, session=None):
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ('', '/')):
            raise StoreError('Invalid private store URL')
        if not key:
            raise StoreError('Missing private store credential')
        self.url = url.rstrip('/') + '/rest/v1/rpc/'
        self.session = session or requests.Session()
        self.headers = {'apikey': key, 'Authorization': 'Bearer ' + key,
                        'Content-Type': 'application/json'}

    def rpc(self, name, payload):
        try:
            r = self.session.post(self.url + name, json=payload,
                                  headers=self.headers, timeout=(10, 45))
            if not r.ok:
                raise StoreError('Private storage request failed')
            data = r.json()
            if not isinstance(data, dict):
                raise StoreError('Invalid private storage response')
            return data
        except (requests.RequestException, ValueError) as exc:
            raise StoreError('Private storage unavailable') from None

    def claim(self, run_id, max_cost, daily_limit):
        return self.rpc('mst_ccg_claim', {'p_run_id': run_id,
                       'p_max_cost': max_cost, 'p_daily_limit': daily_limit})

    def finish(self, run_id, result, charged):
        return self.rpc('mst_ccg_finish', {'p_run_id': run_id,
                       'p_result': result, 'p_charged': charged})
