"""SOCKS5 代理 scheme 選擇（src/fetch_ais_data.py）。

2026-09 事件：AIS 端點被代理商 Byteful 擋掉十天，客服回覆「you are accessing
it via the IP address rather than the hostname」。requests 的 socks5:// 會在
**本機**解析 DNS，代理只看得到 IP，主機名白名單因此比不到。修法是預設改用
socks5h://（由代理端解析），socks5 只留後備。
"""
import fetch_ais_data as f


def _p(port, host='proxy.example.net'):
    return {'host': host, 'port': port, 'user': 'u', 'pass': 'pw'}


# ── build_proxy_url ────────────────────────────────────────────────────────

def test_default_scheme_is_socks5h():
    assert f.PROXY_SCHEMES[0] == 'socks5h'
    assert f.build_proxy_url(_p(1080)) == 'socks5h://u:pw@proxy.example.net:1080'


def test_explicit_scheme_is_honoured():
    assert f.build_proxy_url(_p(1080), 'socks5').startswith('socks5://')


# ── build_proxy_attempts ───────────────────────────────────────────────────

def test_schemes_interleave_per_proxy():
    """兩種 scheme 交錯：同一個代理先試完所有 scheme 才換下一個。

    2026-09-17 的教訓：分段式（先掃完 socks5h 再退回 socks5）把當下唯一能通的
    socks5 排到第 31 順位，前 30 次全浪費。
    """
    pool = [_p(1000), _p(1001)]
    attempts = f.build_proxy_attempts(pool, ('socks5h', 'socks5'), max_attempts=4)
    assert attempts == [
        (pool[0], 'socks5h'), (pool[0], 'socks5'),
        (pool[1], 'socks5h'), (pool[1], 'socks5'),
    ]


def test_both_schemes_covered_within_first_two_attempts():
    pool = [_p(1000 + i) for i in range(50)]
    assert {s for _, s in f.build_proxy_attempts(pool)[:2]} == set(f.PROXY_SCHEMES)


def test_total_attempts_are_bounded():
    pool = [_p(1000 + i) for i in range(50)]
    assert len(f.build_proxy_attempts(pool)) == f.MAX_PROXY_ATTEMPTS


def test_odd_cap_truncates_mid_proxy():
    pool = [_p(1000), _p(1001)]
    attempts = f.build_proxy_attempts(pool, ('socks5h', 'socks5'), max_attempts=3)
    assert len(attempts) == 3 and attempts[-1] == (pool[1], 'socks5h')


def test_single_scheme_stays_single():
    pool = [_p(1000), _p(1001)]
    attempts = f.build_proxy_attempts(pool, ('socks5h',))
    assert {s for _, s in attempts} == {'socks5h'} and len(attempts) == 2


def test_empty_pool_yields_no_attempts():
    assert f.build_proxy_attempts([]) == []


# ── get_proxy_schemes（PROXY_SCHEME 覆寫） ─────────────────────────────────

def test_env_override(monkeypatch):
    monkeypatch.setenv('PROXY_SCHEME', 'socks5')
    assert f.get_proxy_schemes() == ('socks5',)


def test_env_override_accepts_list(monkeypatch):
    monkeypatch.setenv('PROXY_SCHEME', ' socks5h , socks5 ')
    assert f.get_proxy_schemes() == ('socks5h', 'socks5')


def test_blank_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv('PROXY_SCHEME', '   ')
    assert f.get_proxy_schemes() == f.PROXY_SCHEMES


# ── diagnose_proxy.describe：把 SOCKS5 REP 碼從例外訊息裡撈出來 ────────────
# 樣本是 2026-09-17 update-ais.yml 實際 log 的原字串。

def _err(msg):
    return Exception(
        "SOCKSHTTPSConnectionPool(host='mpbais.motcmpb.gov.tw', port=443): Max "
        "retries exceeded with url: /aismpb/tools/geojsonais.ashx (Caused by "
        f'NewConnectionError("SOCKSHTTPSConnection(host=\'mpbais.motcmpb.gov.tw\', '
        f'port=443): Failed to establish a new connection: {msg}"))')


def test_describe_ruleset_block_is_labelled_as_provider_side():
    from diagnose_proxy import describe
    out = describe(_err('0x02: Connection not allowed by ruleset'))
    assert out.startswith('0x02') and '代理商自己擋' in out


def test_describe_refused_is_ambiguous_on_byteful():
    """0x05 在這家代理上同時代表「對方拒絕」與「DNS 解析失敗」。

    實測：拿不存在的域名去解析，回的是 0x05 而不是標準的 0x04，所以說明文字
    不能寫死成「對方拒絕」——那會讓人把 DNS 問題誤判成上游拒絕（本案就發生過）。
    """
    from diagnose_proxy import describe
    out = describe(_err('0x05: Connection refused'))
    assert out.startswith('0x05') and '解析失敗' in out


def test_describe_timeout_without_rep_code():
    from diagnose_proxy import describe
    assert describe(Exception('HTTPSConnectionPool: Read timed out.')) == '逾時'
