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
