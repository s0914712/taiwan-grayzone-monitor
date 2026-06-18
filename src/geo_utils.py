"""共用地理計算函式

供管線各腳本 import（腳本以 `python src/<script>.py` 執行，src/ 自動在 sys.path）：
    from geo_utils import haversine_km, calc_bearing

僅依賴 stdlib（update-ais.yml 環境只安裝 requests + pysocks）。
"""
import math


def haversine_km(lat1, lon1, lat2, lon2):
    """兩點間距離（公里）"""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def calc_bearing(lat1, lon1, lat2, lon2):
    """計算兩點間方位角 (0-360°)"""
    dlon = math.radians(lon2 - lon1)
    lat1r = math.radians(lat1)
    lat2r = math.radians(lat2)
    x = math.sin(dlon) * math.cos(lat2r)
    y = math.cos(lat1r) * math.sin(lat2r) - \
        math.sin(lat1r) * math.cos(lat2r) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


# ── 海里換算 Nautical-mile conversion ────────────────────────────────────────
NM_TO_KM = 1.852


def km_to_nm(km):
    return km / NM_TO_KM


def nm_to_km(nm):
    return nm * NM_TO_KM


# ── 幾何：點到線段 / 折線距離、點在多邊形內 ─────────────────────────────────
def point_to_segment_distance_km(plat, plon, lat1, lon1, lat2, lon2):
    """點到線段的最短距離（公里），以等距投影近似。

    在台灣周邊的小尺度範圍內，將 (lat, lon) 視為平面座標投影即足夠精確，
    再以 haversine 量測投影點的真實球面距離。
    """
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    if dlat == 0 and dlon == 0:
        return haversine_km(plat, plon, lat1, lon1)
    t = ((plat - lat1) * dlat + (plon - lon1) * dlon) / (dlat * dlat + dlon * dlon)
    t = max(0.0, min(1.0, t))
    proj_lat = lat1 + t * dlat
    proj_lon = lon1 + t * dlon
    return haversine_km(plat, plon, proj_lat, proj_lon)


def distance_to_polyline_km(lat, lon, line, closed=False):
    """點到折線（list of (lat, lon)）的最短距離（公里）。

    closed=True 時把折線首尾相連視為封閉環（例如領海基線）。
    空折線回傳 None；單點折線回傳到該點的距離。
    """
    if not line:
        return None
    if len(line) == 1:
        return haversine_km(lat, lon, line[0][0], line[0][1])
    best = None
    n = len(line)
    last = n if closed else n - 1
    for i in range(last):
        a = line[i]
        b = line[(i + 1) % n]
        d = point_to_segment_distance_km(lat, lon, a[0], a[1], b[0], b[1])
        if best is None or d < best:
            best = d
    return best


def point_in_polygon(lat, lon, polygon):
    """點是否在多邊形（list of (lat, lon)）內 — 射線投射法 (ray casting)。

    多邊形視為封閉（首尾自動相連）。邊界上的判定不保證穩定，
    對本專案的海域分類用途已足夠。
    """
    n = len(polygon)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        yi, xi = polygon[i][0], polygon[i][1]   # (lat, lon)
        yj, xj = polygon[j][0], polygon[j][1]
        if ((yi > lat) != (yj > lat)) and \
                (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside
