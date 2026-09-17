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

def test_primary_scheme_sweeps_pool_first():
    pool = [_p(1000 + i) for i in range(5)]
    attempts = f.build_proxy_attempts(pool, ('socks5h', 'socks5'),
                                      max_attempts=5, max_fallback=2)
    assert [s for _, s in attempts[:5]] == ['socks5h'] * 5
    assert [s for _, s in attempts[5:]] == ['socks5'] * 2


def test_total_attempts_are_bounded():
    pool = [_p(1000 + i) for i in range(50)]
    attempts = f.build_proxy_attempts(pool)
    assert len(attempts) == f.MAX_PROXY_ATTEMPTS + f.MAX_FALLBACK_ATTEMPTS


def test_single_scheme_means_no_fallback_pass():
    pool = [_p(1000), _p(1001)]
    attempts = f.build_proxy_attempts(pool, ('socks5h',))
    assert {s for _, s in attempts} == {'socks5h'}


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
