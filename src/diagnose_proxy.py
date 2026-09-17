#!/usr/bin/env python3
"""SOCKS5 代理連線診斷 —— 分辨「被代理商擋」與「被目的端擋」。

背景：2026-09 AIS 停擺。代理商 Byteful 客服說是「用 IP 而非主機名存取」，
改用 socks5h 後錯誤碼從 0x02（Connection not allowed by ruleset，代理自己
擋的）變成 0x05（Connection refused，代理撥出去被拒）。0x05 至少有三種成因，
光看單一結果分不出來，必須有對照組：

  控制組-主機名  socks5h + example.com  → 代理的 domain 路徑本身健康嗎？
  控制組-IP      socks5  + example.com  → 0x02 是「MPB 被列黑名單」還是
                                          「一律不准用 IP 形式連線」？
  出口 IP        socks5h + ipify        → 出口在哪一國？MPB 會擋境外 IP
  目標-主機名    socks5h + MPB :443     → 本案主體
  目標-IP        socks5  + MPB :443     → 原本的失敗方式
  目標-80 埠     socks5h + MPB :80      → 規則是不是綁 443
  DNS 基準       socks5h + 不存在的域名 → 這家代理怎麼回報解析失敗

用法：
    POOL="$(cat pool.txt)" python3 src/diagnose_proxy.py        # 預設測 3 個代理
    POOL=... python3 src/diagnose_proxy.py --proxies 5
"""
import argparse
import os
import re
import sys

import requests
import urllib3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_ais_data import MPB_URL, MPB_HEADERS, build_proxy_url, get_proxy_list

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# SOCKS5 REP 欄位（RFC 1928 §6）
# 注意 0x05：協定上是「連線被拒」，但 2026-09-17 實測 Byteful 的代理把「DNS
# 解析失敗」也回成 0x05（拿一個不存在的域名去試，回的是 0x05 而不是預期的
# 0x04）。所以在這家代理上看到 0x05，先當成「解析不出來」而不是「對方拒絕」——
# 這就是為什麼 DNS 基準那一格非跑不可。
SOCKS5_REPLY = {
    '0x01': '一般性 SOCKS 伺服器失敗',
    '0x02': '代理規則不允許（← 代理商自己擋的，黑名單）',
    '0x03': '網路無法到達',
    '0x04': '主機無法到達（代理端 DNS 解析失敗的標準碼）',
    '0x05': '連線被拒／解析失敗（Byteful 兩種都回這個，看 DNS 基準那格）',
    '0x06': 'TTL 逾時',
    '0x07': '不支援的指令',
    '0x08': '不支援的位址型別',
}

_REP_RE = re.compile(r'(0x0[0-8]):')

GENERIC_HEADERS = {'User-Agent': 'curl/8.0'}

# 出口位置查詢：走代理出去，回傳的就是該出口的國別
EXIT_URL = 'http://ip-api.com/json/?fields=status,country,regionName,query'

# (標籤, scheme, url, 說明)
PROBES = [
    ('控制組-主機名', 'socks5h', 'https://example.com', '代理 domain 路徑是否正常'),
    ('控制組-IP  ', 'socks5', 'https://example.com', '是否「一律禁止 IP 形式」'),
    ('出口位置   ', 'socks5h', EXIT_URL, '出口在哪一國（MPB 會擋境外）'),
    ('目標-主機名', 'socks5h', MPB_URL, '本案主體'),
    ('目標-IP    ', 'socks5', MPB_URL, '原本的失敗方式'),
    ('目標-80埠  ', 'socks5h', MPB_URL.replace('https://', 'http://'), '規則是否綁 443'),
    ('DNS 基準   ', 'socks5h', 'https://nx-does-not-exist-9f3a.example', '解析失敗長什麼樣'),
]


def mask_ip(ip):
    """公開 log 不印完整出口 IP（residential 出口屬於代理商資產）。"""
    parts = str(ip).split('.')
    return '.'.join(parts[:2] + ['x', 'x']) if len(parts) == 4 else '?'


def describe(exc):
    """把 requests 例外翻成「SOCKS REP 碼 + 中文」或原始錯誤摘要。"""
    text = str(exc)
    m = _REP_RE.search(text)
    if m:
        code = m.group(1)
        return f'{code} {SOCKS5_REPLY.get(code, "未知")}'
    for needle, label in (('timed out', '逾時'), ('Timeout', '逾時'),
                          ('SSLError', 'TLS 失敗'), ('ProxyError', '代理錯誤')):
        if needle in text:
            return label
    return text[:90]


def probe(proxy, scheme, url, timeout):
    proxy_url = build_proxy_url(proxy, scheme)
    proxies = {'http': proxy_url, 'https': proxy_url}
    headers = MPB_HEADERS if 'motcmpb' in url else GENERIC_HEADERS
    try:
        r = requests.get(url, headers=headers, proxies=proxies,
                         timeout=timeout, verify=False)
        if url == EXIT_URL:
            try:
                d = r.json()
                return (f'✅ {d.get("country", "?")} / {d.get("regionName", "?")}'
                        f'（出口 {mask_ip(d.get("query"))}）')
            except ValueError:
                pass
        body = r.text[:60].replace('\n', ' ') if len(r.content) < 400 else ''
        return f'✅ HTTP {r.status_code} · {len(r.content):,}B {body}'
    except requests.RequestException as e:
        return f'❌ {describe(e)}'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--proxies', type=int, default=3, help='測幾個代理（預設 3）')
    ap.add_argument('--timeout', type=int, default=20)
    ap.add_argument('--show-host', action='store_true',
                    help='印出代理 host（本機用；公開的 Actions log 不要開）')
    args = ap.parse_args()

    pool = get_proxy_list()
    if not pool:
        return 1

    print(f'\n共 {len(pool)} 個代理，取前 {args.proxies} 個診斷'
          f'（逐項 timeout {args.timeout}s）')
    for p in pool[:args.proxies]:
        who = f'{p["host"]}:{p["port"]}' if args.show_host else f'port {p["port"]}'
        print(f'\n{"="*72}\n代理 {who}\n{"="*72}')
        for label, scheme, url, why in PROBES:
            print(f'  {label} {probe(p, scheme, url, args.timeout)}')
            print(f'             ↳ {why}')

    print(f'''
{"="*72}
判讀
{"="*72}
  控制組-主機名 ✅ 而 目標-主機名 ❌  → 代理正常，是 MPB 這條路徑被擋
  控制組-IP 也回 0x02                → 0x02 是「禁止 IP 形式」的通則，
                                        不是 MPB 被列黑名單（＝客服說的字面意思）
  控制組-IP ✅ 而 目標-IP 回 0x02     → MPB 確實在對方黑名單上
  出口位置不在台灣 + 目標 0x05        → 很可能是 MPB 擋境外 IP，
                                        得跟 Byteful 要台灣出口
  目標-80埠 ✅ 而 443 ❌              → 規則綁在 443
  DNS 基準 與 目標-主機名 同碼         → 代理端根本沒解析成功，不是對方拒絕
                                        （Byteful 把 DNS 失敗回成 0x05，非標準的 0x04）
''')
    return 0


if __name__ == '__main__':
    sys.exit(main())
