#!/usr/bin/env python3
"""
================================================================================
SAR 影像切片取證工具 — Copernicus Data Space (CDSE) S3
================================================================================

報告頁（sar-ais-match.html）出現可疑殘餘暗船時，用本工具把該偵測點周圍的
Sentinel-1 GRD 影像切片抓下來人工確認：
  * 有沒有亮目標（vs 海雜訊/雜訊尖峰）→ 偵測是否可信
  * 目標大概多長 → 粗估船長（可與 AIS 登記船長交叉比對）

這是「本地互動工具」，不進 CI — 一景 GRD 約 1GB，但本工具只透過 S3 range
request 讀取偵測點附近的視窗（數 MB），不下載整景。

用法：
  export CDSE_ACCESS_KEY=...   CDSE_SECRET_KEY=...
  python3 src/fetch_sar_chip.py 24.46 118.59 2026-06-26 \
      [--time 21:55] [--size-km 8] [-o chip.png]

需求：pip install boto3 rasterio numpy matplotlib
（目錄查詢用 OData、匿名免金鑰；影像讀取需要 CDSE S3 金鑰 —
 於 https://eodata-s3keysmanager.dataspace.copernicus.eu 產生）

流程：
1. CDSE OData 目錄（匿名）查該日期、涵蓋該座標的 IW GRDH 產品
2. 以 S3 金鑰列出產品 measurement/ 下的 GeoTIFF，選 VV 極化
3. 讀 GCP、把經緯度轉像素（仿射擬合 + 近鄰 GCP 殘差修正）
4. S3 視窗讀取（rasterio range request）→ 統計亮目標 → 粗估長度
5. 存成標註過的 PNG
================================================================================
"""

import argparse
import math
import os
import sys
from datetime import datetime, timezone

import requests

CDSE_ODATA_URL = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
S3_ENDPOINT = "https://eodata.dataspace.copernicus.eu"
S3_BUCKET = "eodata"

CDSE_ACCESS_KEY = os.environ.get('CDSE_ACCESS_KEY', '').strip()
CDSE_SECRET_KEY = os.environ.get('CDSE_SECRET_KEY', '').strip()

GRD_PIXEL_M = 10.0            # IW GRDH 像素間距（10 m）
TARGET_MAX_PIXELS = 4000      # 連通元件上限（防止把整片亮區當一艘船）
CENTER_SEARCH_HALF = 30       # 在中心 ±30px 內找最亮點作為目標種子


# =============================================================================
# 純函式（可測試、不需相依套件）
# =============================================================================

def build_point_filter(lat, lon, date):
    """OData $filter：該日期、涵蓋該點的 Sentinel-1 IW GRDH 產品。"""
    return (
        "Collection/Name eq 'SENTINEL-1'"
        " and contains(Name,'_IW_GRDH_')"
        f" and OData.CSC.Intersects(area=geography'SRID=4326;"
        f"POINT({lon:.5f} {lat:.5f})')"
        f" and ContentDate/Start ge {date}T00:00:00.000Z"
        f" and ContentDate/Start le {date}T23:59:59.999Z"
    )


def choose_product(products, prefer_time=None):
    """從 OData 回傳的產品挑一個：有 --time 就選成像時刻最接近的。"""
    def start_dt(p):
        s = ((p.get('ContentDate') or {}).get('Start') or '').replace('Z', '+00:00')
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            return None

    cands = [(p, start_dt(p)) for p in products or []]
    cands = [(p, dt) for p, dt in cands if dt is not None and p.get('S3Path')]
    if not cands:
        return None
    if prefer_time:
        hh, mm = prefer_time.split(':')
        target_min = int(hh) * 60 + int(mm)

        def diff(item):
            dt = item[1]
            return abs(dt.hour * 60 + dt.minute - target_min)
        cands.sort(key=diff)
    return cands[0][0]


def pick_measurement_key(keys):
    """從 measurement/ 檔名挑 VV 極化 GeoTIFF（無 VV 退 HH，再退第一個）。"""
    tiffs = [k for k in keys or [] if k.lower().endswith(('.tiff', '.tif'))]
    if not tiffs:
        return None
    for pol in ('-vv-', '-hh-'):
        for k in tiffs:
            if pol in os.path.basename(k).lower():
                return k
    return tiffs[0]


def _fit_affine(gcps):
    """最小平方擬合 col = a·lon + b·lat + c（row 同理）。

    gcps: [(row, col, lon, lat)]，回傳 (col_coef, row_coef)，各為 (a, b, c)。
    """
    def solve3(rows, rhs):
        m = [list(r) + [v] for r, v in zip(rows, rhs)]
        for i in range(3):
            piv = max(range(i, 3), key=lambda r: abs(m[r][i]))
            m[i], m[piv] = m[piv], m[i]
            if abs(m[i][i]) < 1e-12:
                raise ValueError("degenerate GCP geometry")
            for r in range(3):
                if r != i:
                    f = m[r][i] / m[i][i]
                    for c in range(4):
                        m[r][c] -= f * m[i][c]
        return [m[i][3] / m[i][i] for i in range(3)]

    sxx = sxy = sx = syy = sy = n = 0.0
    scx = scy = sc = srx = sry = sr = 0.0
    for row, col, lon, lat in gcps:
        sxx += lon * lon; sxy += lon * lat; sx += lon
        syy += lat * lat; sy += lat; n += 1
        scx += col * lon; scy += col * lat; sc += col
        srx += row * lon; sry += row * lat; sr += row
    normal = [(sxx, sxy, sx), (sxy, syy, sy), (sx, sy, n)]
    col_coef = solve3(normal, (scx, scy, sc))
    row_coef = solve3(normal, (srx, sry, sr))
    return col_coef, row_coef


def latlon_to_pixel(gcps, lat, lon, k_nearest=4):
    """經緯度 → (row, col)。全景仿射擬合 + 近鄰 GCP 殘差 IDW 修正。

    S1 GRD 只有 GCP 網格、無 affine transform；整景單一仿射誤差可達
    公里級，用最近 k 個 GCP 的殘差做距離加權修正後約在百米級 —
    對「切個幾公里見方的視窗」足夠。
    gcps: [(row, col, lon, lat)]
    """
    if len(gcps) < 3:
        raise ValueError("need >=3 GCPs")
    col_coef, row_coef = _fit_affine(gcps)

    def predict(lo, la):
        return (row_coef[0] * lo + row_coef[1] * la + row_coef[2],
                col_coef[0] * lo + col_coef[1] * la + col_coef[2])

    pr, pc = predict(lon, lat)

    # 殘差修正：最近 k 個 GCP（經緯度距離）
    scored = []
    for row, col, lo, la in gcps:
        d2 = (lo - lon) ** 2 + (la - lat) ** 2
        er, ec = predict(lo, la)
        scored.append((d2, row - er, col - ec))
    scored.sort(key=lambda t: t[0])
    wsum = rsum = csum = 0.0
    for d2, dr, dc in scored[:k_nearest]:
        w = 1.0 / (d2 + 1e-12)
        wsum += w; rsum += w * dr; csum += w * dc
    if wsum > 0:
        pr += rsum / wsum
        pc += csum / wsum
    return pr, pc


def _median(vals):
    s = sorted(vals)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def estimate_target_extent(chip, pixel_m=GRD_PIXEL_M):
    """在切片（2D list，振幅 DN）中找中心附近的亮目標並粗估長度。

    門檻：median + 8×MAD 與 3×median 取大者（海面均勻、船是強散射體）。
    自中心 ±CENTER_SEARCH_HALF px 的最亮像素 BFS 連通亮區，
    長度 = 元件包絡對角線 × pixel_m。
    回傳 dict {found, length_m, peak_ratio, n_pixels, row, col} 。
    """
    h = len(chip)
    w = len(chip[0]) if h else 0
    if h < 8 or w < 8:
        return {'found': False}

    flat = [v for line in chip for v in line]
    med = _median(flat)
    mad = _median([abs(v - med) for v in flat]) or 1.0
    threshold = max(med + 8 * mad, med * 3.0, 1.0)

    cr, cc = h // 2, w // 2
    best = None
    for r in range(max(0, cr - CENTER_SEARCH_HALF), min(h, cr + CENTER_SEARCH_HALF)):
        for c in range(max(0, cc - CENTER_SEARCH_HALF), min(w, cc + CENTER_SEARCH_HALF)):
            if best is None or chip[r][c] > chip[best[0]][best[1]]:
                best = (r, c)
    if best is None or chip[best[0]][best[1]] < threshold:
        return {'found': False, 'threshold': threshold,
                'peak_ratio': (chip[best[0]][best[1]] / med if best and med else 0.0)}

    # BFS 連通亮區
    seen = {best}
    queue = [best]
    rmin = rmax = best[0]
    cmin = cmax = best[1]
    while queue and len(seen) <= TARGET_MAX_PIXELS:
        r, c = queue.pop()
        rmin, rmax = min(rmin, r), max(rmax, r)
        cmin, cmax = min(cmin, c), max(cmax, c)
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                nr, nc = r + dr, c + dc
                if 0 <= nr < h and 0 <= nc < w and (nr, nc) not in seen \
                        and chip[nr][nc] >= threshold:
                    seen.add((nr, nc))
                    queue.append((nr, nc))

    length_m = math.hypot(rmax - rmin + 1, cmax - cmin + 1) * pixel_m
    return {
        'found': True,
        'length_m': round(length_m, 1),
        'n_pixels': len(seen),
        'peak_ratio': round(chip[best[0]][best[1]] / med, 1) if med else 0.0,
        'row': best[0], 'col': best[1],
        'saturated': len(seen) > TARGET_MAX_PIXELS,
    }


# =============================================================================
# 網路/影像存取（需 boto3 + rasterio；僅本地互動使用）
# =============================================================================

def query_products(lat, lon, date):
    resp = requests.get(CDSE_ODATA_URL, params={
        '$filter': build_point_filter(lat, lon, date),
        '$top': '20',
        '$orderby': 'ContentDate/Start asc',
    }, timeout=60)
    resp.raise_for_status()
    return resp.json().get('value', [])


def list_measurement_keys(s3path):
    import boto3
    session = boto3.session.Session()
    client = session.client(
        's3', endpoint_url=S3_ENDPOINT,
        aws_access_key_id=CDSE_ACCESS_KEY,
        aws_secret_access_key=CDSE_SECRET_KEY,
        region_name='default')
    prefix = s3path.lstrip('/')
    if prefix.startswith(S3_BUCKET + '/'):
        prefix = prefix[len(S3_BUCKET) + 1:]
    prefix = prefix.rstrip('/') + '/measurement/'
    resp = client.list_objects_v2(Bucket=S3_BUCKET, Prefix=prefix)
    return [obj['Key'] for obj in resp.get('Contents', [])]


def read_chip(key, lat, lon, size_km):
    """rasterio S3 視窗讀取：回傳 (chip 2D list, (row0, col0), (row, col))。"""
    import rasterio
    from rasterio.windows import Window

    env = rasterio.Env(
        AWS_S3_ENDPOINT=S3_ENDPOINT.replace('https://', ''),
        AWS_ACCESS_KEY_ID=CDSE_ACCESS_KEY,
        AWS_SECRET_ACCESS_KEY=CDSE_SECRET_KEY,
        AWS_VIRTUAL_HOSTING='FALSE',
        AWS_REGION='default',
        GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR',
    )
    half_px = int(size_km * 1000 / GRD_PIXEL_M / 2)
    with env:
        with rasterio.open(f's3://{S3_BUCKET}/{key}') as src:
            gcps, _crs = src.gcps
            g = [(p.row, p.col, p.x, p.y) for p in gcps]
            row, col = latlon_to_pixel(g, lat, lon)
            row, col = int(round(row)), int(round(col))
            if not (0 <= row < src.height and 0 <= col < src.width):
                raise ValueError(
                    f"目標像素 ({row},{col}) 超出影像範圍 "
                    f"{src.height}x{src.width} — 該產品可能未涵蓋此點")
            r0 = max(0, row - half_px)
            c0 = max(0, col - half_px)
            win = Window(c0, r0,
                         min(2 * half_px, src.width - c0),
                         min(2 * half_px, src.height - r0))
            arr = src.read(1, window=win)
    return arr.tolist(), (r0, c0), (row, col)


def save_chip_png(chip, center_rc, origin_rc, meta, result, out_path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np

    a = np.asarray(chip, dtype='float64')
    db = 10 * np.log10(np.maximum(a, 1.0))
    fig, ax = plt.subplots(figsize=(8, 8), facecolor='#0a0f1c')
    vmin, vmax = np.percentile(db, 5), np.percentile(db, 99.8)
    ax.imshow(db, cmap='gray', vmin=vmin, vmax=vmax)
    cr = center_rc[0] - origin_rc[0]
    cc = center_rc[1] - origin_rc[1]
    ax.plot(cc, cr, marker='+', color='#00f5ff', markersize=24,
            markeredgewidth=1.2, alpha=0.9)
    if result.get('found'):
        ax.plot(result['col'], result['row'], marker='o', mfc='none',
                color='#ff3366', markersize=18, markeredgewidth=1.2)
    # 1 km 比例尺
    px_km = 1000 / GRD_PIXEL_M
    ax.plot([20, 20 + px_km], [db.shape[0] - 25] * 2, color='w', lw=2)
    ax.text(20, db.shape[0] - 35, '1 km', color='w', fontsize=9)
    title = meta.get('name', '')[:64]
    sub = (f"target ~{result['length_m']} m (peak {result['peak_ratio']}x sea)"
           if result.get('found') else 'no bright target above threshold')
    ax.set_title(f"{title}\n{sub}", color='#e8eef7', fontsize=9)
    ax.axis('off')
    fig.tight_layout()
    fig.savefig(out_path, dpi=140, facecolor=fig.get_facecolor())
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description='SAR 暗船切片取證（CDSE S3）')
    ap.add_argument('lat', type=float)
    ap.add_argument('lon', type=float)
    ap.add_argument('date', help='YYYY-MM-DD（UTC，偵測日期）')
    ap.add_argument('--time', help='偏好成像時刻 HH:MM（例如降軌 21:55）')
    ap.add_argument('--size-km', type=float, default=8.0, help='切片邊長（km）')
    ap.add_argument('-o', '--out', default=None, help='輸出 PNG 路徑')
    args = ap.parse_args()

    if not CDSE_ACCESS_KEY or not CDSE_SECRET_KEY:
        print("❌ 請先設定 CDSE_ACCESS_KEY / CDSE_SECRET_KEY 環境變數\n"
              "   （https://eodata-s3keysmanager.dataspace.copernicus.eu）")
        return 1

    print(f"🔎 查詢 {args.date} 涵蓋 ({args.lat}, {args.lon}) 的 IW GRDH 產品...")
    products = query_products(args.lat, args.lon, args.date)
    product = choose_product(products, args.time)
    if not product:
        print("❌ 該日期查無涵蓋此點的 Sentinel-1 GRD 產品"
              "（可能當天沒過境 — 檢查 data/s1_pass_times.json）")
        return 1
    name = product.get('Name', '')
    print(f"   ✅ {name}")
    print(f"      成像: {(product.get('ContentDate') or {}).get('Start')}")

    keys = list_measurement_keys(product['S3Path'])
    key = pick_measurement_key(keys)
    if not key:
        print(f"❌ 產品內找不到 measurement GeoTIFF（{len(keys)} 個物件）")
        return 1
    print(f"📡 S3 視窗讀取: {os.path.basename(key)}")

    chip, origin, center = read_chip(key, args.lat, args.lon, args.size_km)
    result = estimate_target_extent(chip)
    if result.get('found'):
        print(f"🎯 亮目標: ~{result['length_m']} m、峰值 {result['peak_ratio']}× 海面背景"
              f"（{result['n_pixels']} px）")
        if result.get('saturated'):
            print("   ⚠️ 亮區超過上限 — 可能是陸地/大型結構，長度不可信")
    else:
        print("⚪ 中心附近沒有超過門檻的亮目標 — 偵測可能是雜訊/低 RCS 目標")

    out = args.out or f"sar_chip_{args.date}_{args.lat:.3f}_{args.lon:.3f}.png"
    save_chip_png(chip, center, origin, {'name': name}, result, out)
    print(f"🖼  已存: {out}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
